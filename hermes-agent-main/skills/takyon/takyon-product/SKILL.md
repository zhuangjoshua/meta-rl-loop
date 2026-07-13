---
name: takyon-product
description: Build or improve a Takyon business product app.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, product, website, app, workflow, pricing]
    related_skills: [takyon-market-research, takyon-app-runtime, takyon-distribution, taste-frontend, taste-imagegen-web]
    requires_toolsets: [takyon, takyon-authority]
    requires_tools:
      - business_read_business
      - business_upsert_app_surface_contract
      - business_upsert_app_plan
      - business_generate_site_image
      - business_claude_agent_task
      - business_refresh_product_surface
      - business_check_runtime_capabilities
      - business_invoke_app_action
    routing:
      owns: The whole product surface for one business — first landing/app build, the real gated in-app workflow, and surgical follow-up iterations including authoritative pricing changes.
      when_to_use:
        - the landing page or the `/app` auth/subscription shell is missing, weak, or still starter-shaped (first build)
        - both `/` and the `/app` shell exist but `/app` has no real post-sign-in product workflow yet (flesh out `/app`)
        - the product is already real and one flow, screen, action, conversion step, or price needs a focused pass (surgical iterate)
        - pricing or checkout must change against the authoritative plan catalog
      do_not_use_for:
        - market research, ICP, or offer resets that still need fresh evidence first; use `takyon-market-research`
        - app-runtime backend rails (auth, sessions, entitlements, checkout, webhooks, usage budgets); those are `takyon-app-runtime`
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

# Takyon Product

## Overview

One skill for the whole product surface of a business. It does not prescribe how to build a good product — that craft is the Claude Agent SDK worker's job, given real business context, research, and a clear goal. This skill owns the routing, the delegation choreography, and the binary verification bar.

Infer the PHASE from the current product state instead of choosing a phase-specific skill:

- **First build** — `/` is missing/starter or `/app` is not yet a truthful auth/subscription shell. Seed the surface contract and the monthly plan, then build the landing page and the honest `/app` shell.
- **Flesh out `/app`** — `/` and the `/app` shell exist, but `/app` has no real post-sign-in workflow. Turn `/app` into the real product, adding real action files where backend behavior is needed.
- **Surgical iterate** — the product is already real and one flow, screen, action, conversion step, or price needs a tight, surgical pass — not a rebuild. This phase carries the pricing/plan grandfather rail.

The platform contract for product source — action-file shape, `user` + `entitlements[]` account truth, the forbidden legacy gates (`has_active_subscription`, nested `subscription.status`, ad-hoc `client.account()` parsing), the route skeleton, the no-free-tier / no-trial rules, and the prepared `_takyon/` kit — is injected into every `business_claude_agent_task` worker by code (`plugins/takyon/core.py::_subuser_app_worker_contract_block` and `_subuser_app_kit_contract_block`). Do not restate those rules here or in the instruction; point the worker at the goal and let the injected contract carry the platform invariants.

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
- Supported frontend lane: pinned Vite SPA only (the worker's injected contract enforces the scaffold)
- Worker lane: `business_claude_agent_task` on `product/site/` (the bounded Claude Agent SDK coding worker; not a skill)
- Plan mutation tool: `business_upsert_app_plan`
- Refresh/publish follow-up: `business_refresh_product_surface`
- Action verification: `business_check_runtime_capabilities`, `business_invoke_app_action`, receipts under `metrics/receipts/app-actions/`

## How to Run

1. Call `business_read_business` first to read the business summary, mode, and current product/app state.
2. Read `product/surface.md` and inspect the current `product/site/` source. This is what decides the phase: no real `/` or no truthful `/app` shell → first build; `/app` is shell-only → flesh out `/app`; everything real and the ask is "improve X" → surgical iterate.
3. Read `research/strategy.md` (and any obviously relevant research files) so the product is grounded in the real offer, not invented doctrine.
4. Do the phase-specific seeding (see Phase Notes): first build seeds `business_upsert_app_surface_contract` + `business_upsert_app_plan`; iterate that touches pricing mints the plan version BEFORE the source edit.
5. Delegate ONE bounded `business_claude_agent_task` on `product/site/` for the selected target:
   - `business`, fresh `idempotency_key`, `workspace: product/site`, `refresh_surface: true`.
   - `instruction`: name the concrete product goal for this phase in business terms (the real customer job for `/app`, the specific flow/screen/price to change, the brand/offer to land on the landing page) plus the business + research context. Do NOT restate the platform contract — it is injected.
   - **Initial public landing only:** pass exactly `guidance_skills: ["taste-frontend"]`, `effort: medium`, `max_turns: 60`, and `timeout_ms: 900000`. The pinned full Taste skill owns one continuous Design Read, implementation, build/typecheck, rendered browser review, and final preflight. Before editing the landing, persist the one-line Design Read, exact `DESIGN_VARIANCE`, `MOTION_INTENSITY`, and `VISUAL_DENSITY` values, selected foundation, and durable asset decisions in `product/site/DESIGN.md`. After build/typecheck, serve the build locally and use the installed agent-browser + Chromium rail to render and read screenshots at 1440x900 and 390x844; the first viewport must read as one complete composition with the primary CTA visible and no accidental early next-section intrusion. Fix and rerender until both pass. The 900000ms is one total deadline; any internal recovery can use only the time remaining on that same call and must not start a second Taste worker.
   - **Dense or multi-step product UI:** pass exactly `guidance_skills: []`. Do not pass Taste again and do not use an Open Design template. Read `product/site/DESIGN.md`, then inspect and preserve the landing source, tokens, typography, palette, spacing, motion, and existing assets as the fixed brand source of truth instead of choosing another visual direction. A real workflow build keeps its established capacity: `effort: high`, `max_turns: 90`, `budget_usd: 25.0`, and `timeout_ms: 1800000`; those limits belong to the product continuation and do not change the landing's exact medium/60/900 bound.
   - **Generic `product/site` task:** omitting `guidance_skills` adds no design template and preserves the established Taste `DESIGN.md`. Passing `guidance_skills: []` is the explicit form for bounded continuation work. Never auto-add Taste or another design skill. The injected App Kit contract still owns routes, navigation, auth, checkout, entitlements, account behavior, and actions.
   - Before delegating, inspect existing brand/product media. If the chosen direction genuinely needs imagery and no truthful asset exists, load `taste-imagegen-web`, then call `business_generate_site_image` once per required asset with a stable slug, page-role-specific prompt, and fresh idempotency key. Give the returned `public_path` values to the worker. A typography-led direction may require no generated image; never generate filler.
   - Do not pass `model`. The deployment pins the coding worker for the whole run and rejects model overrides or substitutions. The landing uses its exact bounded values above; the real product-workflow continuation uses its separate established high/90/25/1800000 limits.
6. The worker self-verifies build/typecheck green and self-fixes within its pass (the `.mjs` build gate plus the in-handler warm build-fix retry). Read its structured result, not its prose. A `timed_out`/`blocked` result means the partial edits were preserved — continue from them, do not cold re-delegate from scratch.
7. Follow with `business_refresh_product_surface` (when `refresh_surface: true` the delegated call already runs it) and confirm the publish status.
8. Run the Verification floors below before reporting done. If an action changed, invoke it for real and read the receipt back.

## Phase Notes

### First build (seed the surface + the plan)

- Call `business_upsert_app_surface_contract` once to keep the single surface contract truthful: `source_path` (`product/site`), `routes` (the canonical skeleton), `publish_target`, `runtime_features`, and honest `notes`. Do not duplicate UI/copy/theory into it — it is the tiny shell record only.
- Ensure the canonical monthly paid plan exists with `business_upsert_app_plan` before any UI claims paid subscription is real. Keep `included_ai_budget_microusd <= price_cents * 10_000`.
- Delegate the public landing with the exact Taste-only, medium-effort, 60-turn, 900000ms-total call above. Build a truthful, branded `/`; leave `/app` + `/app/profile` as the honest auth/subscription/account shell. Do not finish the deep `/app` workflow in this phase.

### Flesh out `/app`

- Confirm bootstrap already produced `/` plus the `/app` shell, then delegate with exactly `guidance_skills: []`, `effort: high`, `max_turns: 90`, `budget_usd: 25.0`, and `timeout_ms: 1800000` to turn `/app` into the real product. Read and preserve the canonical `product/site/DESIGN.md` plus the established landing tokens and assets; do not rerun Taste or select a new design template. Let the worker create real `product/site/actions/<name>.ts` files when backend behavior is needed; the injected contract defines their shape.

### Surgical iterate (carries the pricing grandfather rail)

- Start from the existing source. Pick the smallest real target: one page, flow, action, pricing move, paywall/account issue, or conversion blocker. Keep it surgical, not a rebuild. Use the explicit empty guidance list and preserve the Taste-authored DESIGN.md exactly.
- **Pricing is the one change that can silently harm existing customers.** Treat each `plan_key` as an immutable, versioned price offer. `business_upsert_app_plan` FREEZES a live plan's economic terms (`tier`, `price_cents`, `currency`, `billing_interval`, `included_ai_budget_microusd`, `included_action_quota`) once it has active/trialing subscribers and refuses an in-place re-price with a frozen-terms error. That refusal is the guard working — never route around it.
- To change pricing, MINT A NEW `plan_key` version (e.g. `pro-2`) with its own price and Stripe price id; keep the old plan row so existing subscribers stay grandfathered on their frozen snapshot; point new public checkout at the new plan. Do not delete a still-referenced legacy plan row, and do not rely blindly on the default subscribe helper (it picks the first/cheapest published plan and can keep selling the legacy plan) — target the intended public plan explicitly.
- Keep `included_ai_budget_microusd <= price_cents * 10_000`; if the requested included budget exceeds the cap, report the blocker instead of faking it in copy. Moving existing subscribers onto new pricing is a separate billing migration (OpenMeter-owned) that is not available yet — do not fake it.
- For any pricing/plan change, mutate the plan catalog BEFORE the source edit so the UI is generated against true plan state, then refresh.

## Verification

Binary floors only — the worker self-verifies quality and correctness like a normal Claude agent. Do not add brittle semantic assertions.

- **Build + typecheck green** — already gated in the worker `.mjs` build gate and in `business_refresh_product_surface`. A diagnosis without a green build is a failure; the handler classifies an exhausted build failure as `BLOCKED` for hand-patch, not cold re-delegation.
- **Publish landed** — the delegated/refresh result's `surface_refresh.publish.status == "published"` with a real `public_url`. Read it from the structured tool result, not the worker summary.
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
2. Route product craft to the `business_claude_agent_task` worker with business context, research, and a clear goal; do not micromanage build steps in the instruction.
3. Do not restate the code-injected worker contract (action-file shape, `user`+`entitlements[]`, forbidden legacy gates, route skeleton, no-free-tier/no-trial); point the worker at it and let it carry the invariants. Taste applies only to the initial public landing and controls presentation, never the click graph.
4. Keep the public landing to one full Taste-only worker session with `effort: medium`, `max_turns: 60`, one `timeout_ms: 900000` total deadline, and rendered desktop/mobile browser preflight. Real workflow continuation uses no additional design skill, keeps the established high/90/25/1800000 capacity, and inherits the Taste-authored DESIGN.md. Never rerun Taste during later product work.
5. Treat plan catalog mutations as authoritative runtime changes, not marketing copy. Change pricing by minting a new `plan_key` version; keep grandfathered legacy plan rows; keep `included_ai_budget_microusd <= price_cents * 10_000`.
6. Keep access/account/subscription state on the shared runtime helpers; the single offer is one monthly paid subscription (the worker's injected contract enforces no free tier / no trial).
7. Verify only the binary floors (build/typecheck green, publish == published, action invoke + receipt when an action changed); add no new hardcoded semantic checks.
8. Do not claim checkout, pricing, access, publish, or action behavior without tool-backed or receipt-backed proof.
