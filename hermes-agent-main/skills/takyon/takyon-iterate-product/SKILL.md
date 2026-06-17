---
name: takyon-iterate-product
description: Make surgical follow-up improvements to an existing Takyon product after bootstrap and product-workflow are already in place.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, product, iteration, pricing, checkout, app]
    related_skills: [takyon-build-product, takyon-product-workflow, takyon-app-runtime, takyon-market-research]
    requires_toolsets: [takyon]
    requires_tools: [business_read_business, business_upsert_app_plan, business_upsert_app_surface_contract, business_claude_agent_task, business_refresh_product_surface, business_check_runtime_capabilities, business_invoke_app_action]
    routing:
      owns: Surgical post-bootstrap product improvements across the existing Takyon landing, app flow, pricing, and truthful runtime-backed UX.
      when_to_use:
        - the product already exists and needs targeted improvements instead of a ground-up rebuild
        - pricing, checkout, paywall, or account UX must change without drifting away from authoritative runtime truth
        - a product workflow exists but one flow, screen, action, or conversion step is weak and needs a focused pass
      do_not_use_for:
        - first-pass landing-shell bootstrap or the first real `/app` workflow build from scratch
  takyon:
    scope: business
    allowed_roots: [product, metrics]
    output_root: product
    publication:
      - product/surface.md
      - product/site
required_environment_variables: []
required_credential_files: []
---

# Takyon Iterate Product

## Overview

Use this skill after `takyon-build-product` and usually after `takyon-product-workflow` when the product is already real but needs a tight, surgical improvement pass.

This skill is for refinement, not reinvention: sharpen one flow, strengthen conversion, clean up runtime-backed UX, or make a truthful pricing change without rebuilding the whole product.

Pricing is the one change that can silently harm existing customers, so treat each `plan_key` as an immutable, versioned price offer: never re-price a `plan_key` that already has subscribers. `business_upsert_app_plan` enforces this — it refuses an in-place change to a live plan's economic terms (`tier`, `price_cents`, `currency`, `billing_interval`, `included_ai_budget_microusd`, `included_action_quota`) once that plan has active/trialing entitlements. To change pricing you mint a new `plan_key` (a new version) for new signups; existing subscribers stay grandfathered on the frozen row, which is their price snapshot. This is also the forward-compatible model: when OpenMeter later owns plan versions and entitlements, the same `plan_key`-as-version discipline carries over unchanged. Moving existing subscribers onto new pricing is a separate billing migration that is not available yet — do not fake it.

## When to Use

- Use when the current product/site source already exists and the ask is "improve this" rather than "build the product from zero."
- Use when landing, pricing, app-home, profile, or one action-backed flow needs a focused iteration.
- Use when pricing or checkout copy must change together with the real plan catalog.
- Use when the operator wants to raise or adjust pricing for new signups; existing paid users are grandfathered automatically because the plan tool freezes a live plan's economic terms, so you change pricing by minting a new `plan_key` version.
- Do not use this skill for first-pass bootstrap; use `takyon-build-product`.
- Do not use this skill for the first real in-app workflow; use `takyon-product-workflow`.
- Do not use this skill for a major strategy, ICP, or offer reset when the business still needs fresh evidence first; use `takyon-market-research`.

## Quick Reference

- Primary root: `product/`
- Main source targets:
  - `product/site/src/screens/`
  - `product/site/src/components/`
  - `product/site/src/lib/hooks.ts`
  - `product/site/src/lib/takyon.ts`
  - `product/site/actions/*.ts`
  - `product/site/_takyon/surface-context.js`
- Canonical plan mutation tool: `business_upsert_app_plan`
- Canonical refresh/publish follow-up: `business_refresh_product_surface`

## Prerequisites

- Call `business_read_business` first.
- Inspect the current `product/site/` source and `product/surface.md` before deciding what is actually weak.
- Read `research/strategy.md` when the change affects offer, promise, pricing, or positioning.
- If the improvement depends on real backend behavior, be ready to verify it with `business_check_runtime_capabilities` plus a real `business_invoke_app_action` receipt path instead of UI-only confidence.

## How to Run

- Start from the existing source, not from generic product doctrine.
- Decide whether the change is:
  - source-only UX/UI refinement
  - runtime-truth refinement
  - authoritative pricing/plan change
  - action-backed workflow repair
- For UI/source refinement, run one bounded `business_claude_agent_task` on `product/site/` and keep the shared runtime rails intact.
- For pricing or checkout changes, call `business_upsert_app_plan` before claiming the UI is truthful.
- For runtime-surface changes, keep `product/surface.md` accurate with `business_upsert_app_surface_contract`.
- Follow all source and plan changes with `business_refresh_product_surface` so the shared `_takyon/` kit and published surface reflect the real current state.
- If you add or change an action file, verify it with `business_check_runtime_capabilities` and a real `business_invoke_app_action` run before reporting success.

## Procedure

1. Read the business summary, current `product/surface.md`, and the existing `product/site/` source. Confirm this is an iteration task, not a first build.
2. Identify the smallest real target: one page, one flow, one action, one pricing move, one paywall/account issue, or one conversion blocker.
3. If the change touches pricing, treat the plan catalog as authoritative and mutate it with `business_upsert_app_plan`, not just copy in the UI.
4. For monthly paid plans, keep `included_ai_budget_microusd` within the backend cap of `price_cents * 10_000`. If the requested included budget is higher, stop and report the blocker instead of faking it in copy.
5. To change pricing, mint a NEW `plan_key` version — do not try to re-price a live one. `business_upsert_app_plan` freezes a plan's economic terms (`tier`, `price_cents`, `currency`, `billing_interval`, `included_ai_budget_microusd`, `included_action_quota`) once it has active/trialing subscribers and REFUSES an in-place change with a frozen-terms error. Create a new sellable `plan_key` (e.g. `pro-2`) with its own price and Stripe price id, keep the old plan row for grandfathered subscribers, and update the product UI so new checkout routes to the new plan. If the tool returns the frozen-terms error, that is the guard working as designed — switch to a new `plan_key`; never treat it as a bug to route around. Non-economic edits (notes, metadata, Stripe linkage) to a live plan are still allowed, as long as you re-pass its current economic terms unchanged.
6. When grandfathering exists, do not rely blindly on the scaffold defaults for public pricing:
   - `defaultSubscribePlanKey()` picks the first published plan.
   - the published plan list is ordered cheapest first.
   - starter monthly pricing surfaces prefer a plan whose key is literally `monthly`.
   If that would show or sell the grandfathered plan, patch the product screens to select the intended public plan explicitly while leaving auth/account helpers on the shared rails.
7. Do not delete a still-referenced legacy plan row just to hide it from pricing. Active users resolve runtime policy from their entitlement `plan_key`, and missing-plan fallback can collapse them onto another plan with the same tier.
8. Delegate one bounded `business_claude_agent_task` on `product/site/` for the surgical source edits:
   - improve only the target flow
   - preserve shared auth/account/paywall helpers in `src/lib/hooks.ts`
   - preserve runtime client wiring in `src/lib/takyon.ts`
   - keep subscription truth derived from `user` plus `entitlements[]`
   - add or update `product/site/actions/<name>.ts` only when real backend behavior is needed
9. If an action changed, verify it with `business_check_runtime_capabilities` and `business_invoke_app_action`, then read the latest receipt under `metrics/receipts/app-actions/`.
10. Run `business_refresh_product_surface` and re-read the durable outputs that matter: updated source, `product/surface.md`, and `product/site/_takyon/surface-context.js` when plan/pricing truth changed.

## Output Format

- `product/site/` contains the refined product source for the targeted improvement.
- `product/surface.md` reflects truthful routes, runtime notes, and blockers.
- `product/site/_takyon/surface-context.js` reflects the current published plan/runtime context after refresh when pricing or runtime state changed.

## Publication

- Publish refined source to `product/site/`.
- Publish refreshed truth to `product/surface.md`.
- If runtime-backed behavior changed, prove it with the real tool result and receipt path rather than prose alone.

## Common Pitfalls

- Rebuilding large parts of the product when only one weak flow needed attention
- Changing copy to a new price without changing the authoritative app plan
- Trying to re-price a live `plan_key`; the tool freezes economic terms once it has subscribers, so mint a new `plan_key` version instead of fighting the gate
- Forgetting that the default subscribe helper picks the first published plan, which can keep selling the cheapest legacy plan
- Deleting the old plan row even though active entitlements still reference it
- Assuming the included AI budget cap is `10%` of subscription price; the backend cap is the full monthly plan price in microusd
- Reintroducing legacy `has_active_subscription` or nested `subscription.status` parsing instead of using the shared helpers
- Claiming an action-backed improvement worked without a real invoke plus receipt read-back

## Verification Checklist

- [ ] `product/site/` already existed and the work stayed surgical instead of becoming a full rebuild
- [ ] Any pricing claim in the UI matches the plan catalog changed through `business_upsert_app_plan`
- [ ] Any monthly included AI budget stays within the backend-enforced `price_cents * 10_000` cap
- [ ] A pricing change for new signups was done by minting a new `plan_key` version, not by re-pricing a plan that already has subscribers (the tool enforces this; a frozen-terms refusal means switch to a new `plan_key`)
- [ ] Public subscribe CTAs point at the intended sellable plan, not the accidental cheapest legacy plan
- [ ] `product/surface.md` matches the new truth
- [ ] `product/site/_takyon/surface-context.js` was refreshed and re-checked when plans or runtime context changed
- [ ] Any changed action was verified with a real invoke and receipt read-back

## Rules

1. Iterate from the existing product truth; do not restart the product unless the operator asked for that.
2. Treat plan catalog mutations as authoritative runtime changes, not marketing copy.
3. Change pricing by minting a new `plan_key` version; `business_upsert_app_plan` freezes a live plan's economic terms and refuses an in-place re-price, so never rely on overwriting a subscribed `plan_key`.
4. Keep legacy plan rows while live entitlements still depend on them.
5. Keep access/account/subscription state on the shared runtime helpers in `src/lib/hooks.ts`.
6. Do not advertise a free plan, free tier, or free trial; the Takyon app runtime does not support that shape.
7. Do not claim checkout, pricing, access, or action behavior without tool-backed or source-backed proof.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Requested included AI budget is larger than plan price | Report the backend cap truthfully and lower the included budget or raise the plan price |
| New users should pay more but old subscribers must stay put | Create a new sellable `plan_key` and Stripe price, then route public subscribe UI to it; old subscribers are grandfathered automatically because the live plan's economic terms are frozen |
| `business_upsert_app_plan` refused with a frozen-terms error | Expected — the plan has active subscribers, so its economic terms are locked. Mint a new `plan_key` version for the new price and route new checkout to it; the old subscribers stay grandfathered. Migrating them onto new pricing is a separate billing migration (OpenMeter-owned; not available yet) |
| The UI still sells the old cheapest plan after adding a new one | Stop using the blind default subscribe helper for that screen and target the intended public plan explicitly |
| A grandfathered plan disappeared from the catalog | Restore the legacy plan row before active users fall back onto another tier-matched plan |
| The improvement added an action but verification is missing | Run `business_check_runtime_capabilities`, invoke the action for real, and read back the receipt before reporting success |
