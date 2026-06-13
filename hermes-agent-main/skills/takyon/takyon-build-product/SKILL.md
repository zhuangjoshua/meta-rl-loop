---
name: takyon-build-product
description: "Bootstrap one Takyon business product site: seed the scaffold and wire honest shared rails."
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, product, website, app, offer]
    related_skills: [takyon-market-research, takyon-app-runtime, takyon-product-workflow, takyon-distribution]
    requires_toolsets: [takyon]
    requires_tools: [business_read_business, business_upsert_app_surface_contract, business_claude_agent_task, business_refresh_product_surface]
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

# Takyon Build Product

## Overview

Use this skill to **bootstrap** one business product site: create `product/site`, seed the pinned Vite scaffold, wire the honest shared app-runtime basics the first shell needs (auth/session, account, checkout/subscription, canonical billing/plan setup), and record the truthful source-path and not-built state.

This skill is bootstrap-only. It does not define what the product *is*. Product semantics — the customer-experience shape, the runtime rail selection (`runtime_features`), named backend actions, and the in-app `product_workflow` — are owned by `takyon-product-workflow`, which is the first place the real product is defined. If the real customer product is not built yet, the truthful state is `workflow_pending`/blocked, not a generic placeholder product or a taxonomy-rich fake shell.

## When to Use

- Use after research when the business needs a real product or site surface.
- Use when the current source is too weak, misleading, unpublished, or mismatched to the offer.
- Use when the operator asks to build, publish, or repair the business product surface.
- Do not use for auth, entitlements, billing, or checkout wiring by itself; route that work through `takyon-app-runtime`.

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/surface.md`, `product/site/`
- Best call points: post-research product creation, surface repair, publication work
- Publication lane: publish from `product/site/`; reflect refreshed state in `product/surface.md`
- Frontend lane: workspace creation seeds `product/site/` from the canonical pinned scaffold at `plugins/takyon/subuser_app_kit/scaffold/` (a pre-verified static Vite + React + TS + Tailwind SPA with rails, hooks, ui components, and support pages pre-wired, business name/description substituted at seed time). The Vite scaffold is the only supported lane and is seeded automatically — it is not a CEO-authored field. If you encounter an old Next/AppKit tree, treat it as upgrade input onto this lane, not as a separate supported lane.
- Tool names used by this skill: `business_read_business`, `business_read_file`, `business_list_files`, `business_check_runtime_capabilities`, `business_upsert_app_surface_contract`, `business_create_workspace`, `business_write_file`, `business_patch_file`, `business_claude_agent_task`, `business_refresh_product_surface`

## Prerequisites

- The Takyon toolset must be available.
- Start with canonical business state: `business_read_business`, then load `product/surface.md` with `business_read_file` if it already exists.
- If the source path, framework, or local toolchain is unclear, use `business_check_runtime_capabilities` before acting as if a build stack already exists.
- If the business has no surface contract yet, create it first with `business_upsert_app_surface_contract`.
- For substantial `product/site/` work, prefer one delegated `business_claude_agent_task` call and let that worker finish the source itself. The runtime may perform one automatic local source/build repair retry before the call returns. Do not default to CEO source inspection, local hand-patching, or a second delegated pass in the same turn unless the worker explicitly returns `BLOCKED:` or the operator asked for manual repair.
- During bootstrap for a software business, establish a real `product/site/` source path before creating extra runtime notes or billing prose.
- During bootstrap, do not call `business_invoke_app_action`, do not try to create or verify a real app session/account loop, and do not simulate the post-sign-in product workflow. Bootstrap owns the shell and truthful blocked state only; action invocation, app-account verification, and real workflow loops belong to `takyon-product-workflow` after the shell exists.

## References

- `references/surface-rules.md`

## Templates

- `templates/surface.md`

## How to Run

- Call `business_read_business` first to inspect the current business summary, product surface, and publication state.
- Use `business_read_file` and `business_list_files` to inspect `product/surface.md` and the current source tree before rewriting anything.
- Use `business_upsert_app_surface_contract` to set or repair the bootstrap-owned fields: the canonical source path, routes, publish target, and done gate. Do not author the product's rail selection (`runtime_features`), customer-experience shape, or any app-shape taxonomy at bootstrap — those are owned by `takyon-product-workflow`.
- For any monthly paid surface, create or update the canonical `monthly` app plan with `business_upsert_app_plan` before claiming a paid CTA is live. Set both `price_cents` and `included_ai_budget_microusd` on that plan; the included AI budget is a real plan parameter and must stay between `0` and the monthly price expressed in microusd (`price_cents * 10_000`). Do not leave pricing as frontend copy with no real `app_plan_policies` row behind it.
- Keep the first bootstrap seed minimal and executable: a real `product/site` source path and the minimal access-shell routes only. Do not invent speculative product routes (`/editor`, `/documents`), in-app workflow, free-tier/trial copy, or customer-visible debug/blocker language. The truthful not-built state is `workflow_pending`/blocked, never a generic placeholder product.
- Do not preseed `actions` (or any deeper product rail) on the bootstrap seed. Record the truthful not-built state and leave defining what the product is — its workflow doctrine and any named actions — to `takyon-product-workflow`.
- Read `research/` broadly, especially `research/strategy.md`, to pick an honest scaffold seed and a truthful first offer. Do not author the customer-experience IA (`surface_goal`, `conversion_model`, `required_sections`, `required_app_tabs`) or the in-app `product_workflow` at bootstrap; defining what the product is belongs to `takyon-product-workflow`.
- Once the shell is truthful enough to hand off, stop gathering optional market color and continue into `takyon-product-workflow`. Do not spend the rest of the turn on extra research or pseudo-verification when the real missing work is the actual product build.
- Use `business_create_workspace`, `business_write_file`, and `business_patch_file` for tiny local product-file edits, especially receipts and small source fixes under `product/`.
- Use `business_claude_agent_task` only when the delegated worker lane is available and the job is meaningfully larger than a direct first-surface build. Product/site work injects the full design-pack set plus a choose-one-direction directive by default, so you do not need to pass `guidance_skills` for ordinary design work. Pass an explicit subset only to narrow the direction, or `guidance_skills: []` to opt out.
- For `product/site/`, delegate one bounded worker call and let that worker own the source/build loop. The worker will receive the prepared shared subuser app kit under `product/site/_takyon/`; build the business-specific UI around that substrate instead of reinventing app-plane rails.
- Once the access shell exists and the operator wants to build or evolve what the customer actually does after sign-in, route that deeper gated workflow work through `takyon-product-workflow` instead of stretching this skill past the first surface. When the operator asked to build the real product from scratch, treat that handoff as part of the same overall job: finish the shell, then continue into `takyon-product-workflow` in the same turn unless an exact blocker stops you.
- If starter source already exists under `product/site/src/`, treat it as canonical scaffolding, not as disposable scratch UI. Preserve the supported AppKit rail helpers in `src/lib/takyon.ts` and `src/lib/hooks.ts`. If the current source is still an old Next/AppKit tree, upgrade it onto the scaffold lane instead of preserving it as a second path.
- Default app-like bootstrap routes are `/`, `/app`, and `/app/profile`. Keep sign-in/subscribe/account access inside the gated `/app` shell, and keep subscription/account state visible on `/app/profile` unless the surface contract explicitly needs more.
- Treat preset `/privacy`, `/terms`, `/faq`, and `/articles` pages as plain support pages rather than default design targets, and do not spend bootstrap/design time on them unless explicitly asked.
- Product pages that require paid access should stay inside the gated `/app` boundary so entitlement remains the route-level boundary: use the scaffold shell under `src/screens/app-layout.tsx` and `src/screens/app-home.tsx`.
- When first publish speed matters, prefer the smallest dependency-light source that can verify and publish quickly. Do not default to a heavy framework if a static or minimal access shell is enough for the first surface.
- Use `business_refresh_product_surface` only when there is real source to publish. Treat its blocker output and receipt as truth.
- During bootstrap, once `product/site/` exists with real source, complete one refresh/publish pass before later auth/customer/outreach follow-on work. If that refresh hits a local source/build blocker, the delegated runtime may retry once with the exact blocker before it returns control.

## Procedure

1. Call `business_read_business` and identify the current offer, source path, publish target, and blocker state. If `product/surface.md` exists, load it with `business_read_file`.
2. If there is no canonical surface contract or the source path is wrong, call `business_upsert_app_surface_contract` first. Default to a real source path under `product/site/`; do not invent a publish target without recording it.
3. Do not author `runtime_features` at bootstrap — it is the one shared-rail contract and is declared during `takyon-product-workflow`. The honest access shell (auth/session, account, checkout) is wired by the seeded scaffold and the bootstrap rail/plan tools, not by pre-declaring rails. Named backend actions and `generate` are decided later by `takyon-product-workflow`, not here.
4. Read `research/` broadly before deciding the bootstrap surface, and explicitly inspect `research/strategy.md` by name plus any other relevant files in `research/`. Use that evidence to pick an honest scaffold seed and a truthful first offer.
5. Do not author the customer-experience IA (`surface_goal`, `conversion_model`, `required_sections`, `required_app_tabs`) or the in-app `product_workflow` at bootstrap — defining what the product is belongs to `takyon-product-workflow`. On the first app-shell seed, default to the smallest paid monthly conversion shape; use paid CTA language by default, not `Start free` or `$0` copy.
6. If a shared rail is already known-bad at bootstrap, record truthful per-rail `rail_state` on the contract (`declared`, `live`, `blocked`, or `broken`); shared AppKit rails otherwise start as `declared`. Do not set app-shape taxonomy (`app_mode`, `subscription_style`, `api_mode`, `frontend_stack`) — those fields no longer exist on the contract; the Vite scaffold is the platform default lane and product semantics are owned by `takyon-product-workflow`.
7. If the surface presents a paid monthly CTA or named monthly price, call `business_upsert_app_plan` for plan key `monthly` before final refresh/publish so checkout has a real canonical plan object. Set `price_cents` and `included_ai_budget_microusd` together instead of leaving the included AI budget implicit or hardcoded in UI copy. For this monthly shape, customer-facing pricing should read as paid-only unless the operator explicitly asks for another offer structure.
8. If `product/site/` or the expected source directory does not exist, create it with `business_create_workspace` and write the initial structure there before claiming product work is underway. Workspace creation seeds the pinned Vite scaffold automatically — the lane is the platform default, so there is no lane field to set first; if you ever seed manually, copy from `plugins/takyon/subuser_app_kit/scaffold/` excluding `_takyon/`, `node_modules/`, and `dist/`, and keep `package.json` plus `package-lock.json` pinned as shipped. The lane is static-only — any server route handler, `pages/api` file, Next config, or server-framework import in product source becomes an exact refresh blocker. Replace `src/tokens.css` and the Tailwind theme from the design brief in the same pass: tokens left byte-identical to the scaffold placeholder are a visible refresh advisory and a do-not-publish signal.
9. If the local runtime, package manager, or framework capability is unclear, call `business_check_runtime_capabilities` and only proceed with the stack the runtime actually supports.
10. During bootstrap for software businesses, default the first surface mode to `app_shell`, not `landing_page_only`. Only choose landing-only when the operator or current evidence explicitly calls for a validation/offer-page-first surface. When the surface is app-like, record `/app` explicitly in `required_routes` instead of relying on an implied shell, but let that first `/app` stop at sign-in, subscribe, and account access unless the contract explicitly requires more product workflow.
11. On that first app-shell/bootstrap pass, do not add `actions` preemptively just because most real products will later need it. Route the deeper post-sign-in workflow and its product-specific backend leaves — including any named actions — through `takyon-product-workflow`.
12. Write or patch `product/surface.md` so it records the truthful bootstrap state: source path, seeded scaffold, the honest shared rails that were wired (auth/account/checkout/plan), what works now, and the not-built workflow state (`workflow_pending`/blocked). Leave the rail selection, customer-experience shape, and in-app workflow for `takyon-product-workflow` to record. That same contract is what refreshes the prepared `_takyon/` kit and what the worker receives as product-site UI truth.
13. Decide whether the source work fits inside one CEO turn. For a substantial first site/access-shell publish, default to one delegated `business_claude_agent_task` call under `product/site/` so the worker can finish the source, local build/test, and cleanup inside its isolated lane. If the worker explicitly returns `BLOCKED:` or hits a provider/runtime gate, record the blocker instead of defaulting to CEO source repair in the same turn.
14. Design guidance is automatic for product/site work: the full design-pack set and a choose-one-direction directive are injected by default, so you do not pass `guidance_skills` for ordinary design work. Pass an explicit subset only to constrain the direction, or `guidance_skills: []` to opt out.
15. If the operator asked for publication and the source is real, call `business_refresh_product_surface` before later auth/customer/outreach follow-on work. If it returns a blocker, write that blocker back into `product/surface.md` and stop the same-turn source loop instead of defaulting to CEO repair passes.
16. During bootstrap, treat `product/site/` plus a current `product/surface.md` source path as the product completion threshold before letting extra runtime notes become the main visible outcome. Once the first public surface is live, continue the rest of bootstrap in the same turn.
17. When the operator asked for the real product from scratch, the next step after truthful shell bootstrap is `takyon-product-workflow`, not app-action probing. Hand off immediately once `product/site` and the canonical shell state exist.

### Shared style packs

All shared style packs below are injected alongside `claude-design` by default for product/site work, and the worker is told to choose one coherent direction from the brief rather than blending them. This catalog is reference for which pack tends to suit which product when you want to narrow the direction with an explicit `guidance_skills` subset:

- `claude-design-openai`: calm serious for AI tools, research, prosumer, and productivity surfaces
- `claude-design-stripe`: premium B2B, commercial, fintech, infra
- `claude-design-superhuman`: premium productivity, focus, speed
- `claude-design-vibrant`: fun consumer, creator, colorful prosumer
- `claude-design-doodle`: whimsical playful consumer, pets, kids, deliberately silly products

## Output Format

- `product/surface.md` should describe what bootstrap actually wired (seeded scaffold, honest shared rails), what is blocked, and the truthful not-built workflow state.
- `product/site/` should contain real source, not notes or scratch docs presented as source.

## Publication

- The canonical local source path is `product/site/`.
- Publish the surface contract and current state to `product/surface.md`.
- Public or local publication state must be reflected in `product/surface.md`.
- Do not claim a deployed URL, working route, or finished publish unless the runtime or tool receipts actually support it.

## Common Pitfalls

- Writing polished copy while leaving the real source or route missing
- Claiming runtime-backed product behavior that only exists in prose
- Splitting the actual product surface across random directories
- Treating `business_claude_agent_task` as a second owner instead of the implementation lane underneath this skill
- Claiming publication state without an explicit `business_refresh_product_surface` result or visible receipt
- Falling back to CEO hand-patching right after a delegated `product/site/` worker call instead of letting the worker finish, use its automatic local repair retry, or block cleanly

## Verification Checklist

- [ ] `product/site/` contains usable source, not only notes
- [ ] Any claimed publication state is backed by `business_refresh_product_surface` output or visible receipts
- [ ] Any runtime-dependent feature is routed through `takyon-app-runtime`, and declared shared rails are not pre-disabled without an explicit blocked/broken reason or a real request failure
- [ ] Bootstrap did not substitute extra runtime notes for missing `product/site/` source

## Rules

1. Do not present a placeholder, generic, or not-built product as a real finished one; the truthful not-built state is `workflow_pending`/blocked.
2. Use real source files when the operator asked for a built surface.
3. Keep the surface business-owned and customer-ready.
4. Use app-runtime rails when the product needs shared backend behavior.
5. For substantial `product/site/` implementation, prefer one delegated `business_claude_agent_task` call. Do not default to CEO source inspection or a second delegated pass in the same turn unless the worker explicitly returns `BLOCKED:` or the operator asked for manual repair.
6. For software businesses, bootstrap should default to a real app shell. Do not quietly substitute a landing-only waitlist page unless the operator or evidence explicitly calls for landing-page-only validation mode.
7. Do not ship customer-facing copy that frames the surface as a stub, demo, placeholder, scaffold, or developer preview.
8. In customer-facing design and copy, default to capability language instead of vendor/model labels. If the operator explicitly wants named model positioning, use current names accurately; do not leak stale labels like `GPT-4o-mini` into the product surface.
9. When `business_upsert_app_surface_contract` fails or `business_claude_agent_task` reports the worker runtime unavailable, record the exact blocker on the business and stop. Do not hand-write `product/site/` source in-loop beyond the seeded starter kit, and do not hand-author `surface.md` as a workaround for a failed contract tool — the contract, worker lane, and refresh gate are one path, and skipping any of them ships unreviewed rail-shadowing code.
10. On the `vite_react_ts` lane, never add server code to product source (no route handlers, no `pages/api`, no Next config, no express/fastify/hono/koa) — server logic belongs in declared actions — and never publish with scaffold placeholder tokens still in place.
11. Do not use this skill to probe app actions, magic-link flows, or real app-account state. Those are downstream workflow/runtime checks, not bootstrap acceptance criteria.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Publish is blocked | Record the exact blocker and keep the local product surface aligned with the current publish state |
| The product needs backend behavior | Route through `takyon-app-runtime` before inventing custom state |
