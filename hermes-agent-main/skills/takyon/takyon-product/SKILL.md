---
name: takyon-product
description: >-
  Build or improve one business web product, including its public landing surface and real signed-in
  customer workflow, then verify the build and publication result. Use when the product is missing,
  starter-shaped, incomplete, or needs a focused iteration. Do not use for market research, channel
  distribution, or backend-rail invention.
---

# Takyon Product

## Overview

One skill for the whole product surface of a business. The primary Claude Agent SDK session applies
the craft directly, using real business context, research, and one clear goal. This skill owns
routing, implementation choreography, and the binary verification bar.

Infer the PHASE from the current product state instead of choosing a phase-specific skill:

- **First build** — `/` is missing/starter or `/app` is not yet a truthful auth/subscription shell. Seed the surface contract and the monthly plan, then build the landing page and the honest `/app` shell.
- **Flesh out `/app`** — `/` and the `/app` shell exist, but `/app` has no real post-sign-in workflow. Turn `/app` into the real product, adding real action files where backend behavior is needed.
- **Surgical iterate** — the product is already real and one flow, screen, action, conversion step, or price needs a tight, surgical pass — not a rebuild. This phase carries the pricing/plan grandfather rail.

The platform contract for product source — action-file shape, `user` + `entitlements[]` account truth, the forbidden legacy gates (`has_active_subscription`, nested `subscription.status`, ad-hoc `client.account()` parsing), the route skeleton, the no-free-tier / no-trial rules, and the prepared `_takyon/` kit — is injected into the primary SDK session by runtime policy. Do not restate or weaken it; apply this skill directly inside that contract.

## When to Use

- Use for the first real customer surface after research: build `/`, keep `/app` and `/app/profile` as the truthful auth/subscription/account shell.
- Use when `/` exists but `/app` is still only the shell and needs to become the real post-sign-in product.
- Use when the product is real but one page, flow, action, paywall/account issue, or conversion blocker needs a surgical pass.
- Use when pricing or checkout copy must change together with the authoritative plan catalog; raising price for new signups grandfathers existing subscribers automatically (see Phase Notes).
- Do not use for a strategy/ICP/offer reset that needs fresh evidence first; use `takyon-market-research`.
- Do not hand-roll app-runtime backend rails here; auth, sessions, entitlements, checkout, webhooks, and usage budgets are `takyon-app-runtime`.

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/surface.md`, `product/site/`
- Supported frontend lane: pinned Vite SPA only (the injected session contract enforces the scaffold)
- Implementation lane: this primary SDK session edits `product/site/` through scoped business
  read/write/patch tools; it does not spawn another agent or worker
- Plan mutation tool: `business_upsert_app_plan`
- Refresh/publish follow-up: `business_refresh_product_surface`
- Action verification: `business_check_runtime_capabilities`, `business_invoke_app_action`, receipts under `metrics/receipts/app-actions/`

## How to Run

1. Call `business_read_business` first to read the business summary, mode, and current product/app state.
2. Read `product/surface.md` and inspect the current `product/site/` source. This is what decides the phase: no real `/` or no truthful `/app` shell → first build; `/app` is shell-only → flesh out `/app`; everything real and the ask is "improve X" → surgical iterate.
3. Read `research/strategy.md` (and any obviously relevant research files) so the product is grounded in the real offer, not invented doctrine.
4. Do the phase-specific seeding (see Phase Notes): first build seeds `business_upsert_app_surface_contract` + `business_upsert_app_plan`; iterate that touches pricing mints the plan version BEFORE the source edit.
5. Implement the selected target directly in this bounded primary SDK session:
   - Invoke the native `design-taste-frontend` skill for the initial public landing and any
     marketing-surface redesign. Its full method is already installed in the release plugin.
   - Invoke Taste with facts, not a caller-authored design solution: pass only the business goal,
     audience, offer, explicit user requirements, existing source/assets, and technical/runtime
     constraints. Do not prescribe a hero type, page anatomy, section order, card count, design
     dials, palette, typography, image count, or motion; the unchanged native skill chooses those.
   - For dense or multi-step product UI, honor Taste's scope boundary and follow the injected App Kit
     contract rather than forcing marketing-page layouts onto the app.
   - Read, write, and patch `product/site/` through the scoped business file tools. Preserve useful
     existing work and the established design direction.
   - Use the Safebox-gated `business_generate_site_image` capability when Taste decides original
     imagery has a defined page role. Images are optional; never generate filler or fabricate product
     UI inside an image.
   - Render and inspect the result when browser/screenshot capability is available, then revise
     obvious implementation misses. This is a normal nonblocking craft loop, not a publication gate.
   - Never invoke a nested agent, subagent, or second model session. Model, turn, spend, and
     wall-clock ceilings belong to the calling runtime.
6. Run the source/build/type verification available through the scoped surface refresh. The first
   API error, unchanged deterministic failure, timeout, or explicit blocker ends the attempt; do not
   create a replacement worker.
7. Call `business_refresh_product_surface` with a fresh idempotency key and confirm the structured
   publish status.
8. Run the Verification floors below before reporting done. If an action changed, invoke it for real and read the receipt back.

## Phase Notes

### First build (seed the surface + the plan)

- Call `business_upsert_app_surface_contract` once to keep the single surface contract truthful: `source_path` (`product/site`), `routes` (the canonical skeleton), `publish_target`, `runtime_features`, and honest `notes`. Do not duplicate UI/copy/theory into it — it is the tiny shell record only.
- Ensure the canonical monthly paid plan exists with `business_upsert_app_plan` before any UI claims paid subscription is real. Keep `included_ai_budget_microusd <= price_cents * 10_000`.
- Apply native `design-taste-frontend` directly to build a truthful, branded `/`; leave `/app` + `/app/profile` as the honest auth/subscription/account shell. Do not finish the deep `/app` workflow in this phase.

### Flesh out `/app`

- Confirm bootstrap already produced `/` plus the `/app` shell, then implement the real product
  directly in this session. Honor Taste's product-UI scope boundary, preserve useful existing work,
  and create real `product/site/actions/<name>.ts` files when backend behavior is needed; the
  injected contract defines their shape.
- Implement requested inputs and behaviors literally. Never replace an upload or another requested
  capability with a URL field, "coming soon", or a placeholder; declare and use the corresponding
  canonical AppKit runtime rail.

### Surgical iterate (carries the pricing grandfather rail)

- Start from the existing source. Pick the concrete target: one page, flow, action, pricing move, paywall/account issue, or conversion blocker. Preserve the established direction; do not inject design rules into the worker prompt.
- **Pricing is the one change that can silently harm existing customers.** Treat each `plan_key` as an immutable, versioned price offer. `business_upsert_app_plan` FREEZES a live plan's economic terms (`tier`, `price_cents`, `currency`, `billing_interval`, `included_ai_budget_microusd`, `included_action_quota`) once it has active/trialing subscribers and refuses an in-place re-price with a frozen-terms error. That refusal is the guard working — never route around it.
- To change pricing, MINT A NEW `plan_key` version (e.g. `pro-2`) with its own price and Stripe price id; keep the old plan row so existing subscribers stay grandfathered on their frozen snapshot; point new public checkout at the new plan. Do not delete a still-referenced legacy plan row, and do not rely blindly on the default subscribe helper (it picks the first/cheapest published plan and can keep selling the legacy plan) — target the intended public plan explicitly.
- Keep `included_ai_budget_microusd <= price_cents * 10_000`; if the requested included budget exceeds the cap, report the blocker instead of faking it in copy. Moving existing subscribers onto new pricing is a separate billing migration (OpenMeter-owned) that is not available yet — do not fake it.
- For any pricing/plan change, mutate the plan catalog BEFORE the source edit so the UI is generated against true plan state, then refresh.

## Verification

Binary floors only. Use the normal direct-agent craft and inspection loop; do not add brittle
semantic assertions or subjective publication gates.

- **Build + typecheck green** — enforced by `business_refresh_product_surface`. A diagnosis without a
  green build is a failure.
- **Publish landed** — the refresh result's `surface_refresh.publish.status == "published"` with a
  real `public_url`. Read it from the structured tool result, not assistant prose.
- **If an action changed** — run one real `business_check_runtime_capabilities` to confirm it certifies, one real `business_invoke_app_action`, then read the latest receipt under `metrics/receipts/app-actions/`. UI-only confidence is not proof.

Do not report done until the publish floor (and the action floor, when an action changed) is read back from real tool results.

## Output Format

- `product/site/` contains the real product source for the phase (landing + shell for first build; real `/app` workflow when fleshing out; the targeted refinement when iterating), plus any needed `product/site/actions/<name>.ts`.
- `product/surface.md` reflects the current source path, routes, publish state, runtime notes, and blockers.
- `product/site/_takyon/surface-context.js` reflects the current published plan/runtime context after refresh when plan/pricing truth changed.

## Publication

- Publish product source to `product/site/`.
- Publish refreshed truth to `product/surface.md`.
- A required publication path is not "done" until `surface_refresh.publish.status == "published"` and the live `public_url` are present in the tool result.
- If runtime-backed behavior changed, prove it with the real `business_invoke_app_action` result and the receipt path under `metrics/receipts/app-actions/`, not prose.

## Rules

1. Infer the phase from current product state; do not restart a real product unless the operator asked for that.
2. Apply product craft directly in the primary SDK session with business context, research, and a
   clear goal; never spawn a worker.
3. Do not restate or weaken the code-injected session contract (action-file shape,
   `user`+`entitlements[]`, forbidden legacy gates, route skeleton, no-free-tier/no-trial).
   Native Taste controls presentation, never the click graph.
4. Use one bounded primary session for the selected phase. Invoke native Taste as part of that same
   session; do not install skills per business or create a separate design agent.
5. Treat plan catalog mutations as authoritative runtime changes, not marketing copy. Change pricing by minting a new `plan_key` version; keep grandfathered legacy plan rows; keep `included_ai_budget_microusd <= price_cents * 10_000`.
6. Keep access/account/subscription state on the shared runtime helpers; the injected session
   contract enforces the single monthly paid offer with no free tier or trial.
7. Verify only the binary floors (build/typecheck green, publish == published, action invoke + receipt when an action changed); add no new hardcoded semantic checks.
8. Do not claim checkout, pricing, access, publish, or action behavior without tool-backed or receipt-backed proof.
