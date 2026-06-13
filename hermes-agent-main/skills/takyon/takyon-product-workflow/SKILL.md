---
name: takyon-product-workflow
description: Build or evolve the real gated in-app product workflow under `product/site/` and record that workflow on the canonical surface contract.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, product, workflow, app, mvp]
    related_skills: [takyon-build-product, takyon-app-runtime, takyon-market-research]
    requires_toolsets: [takyon]
    requires_tools: [business_read_business, business_upsert_app_surface_contract, business_claude_agent_task, business_refresh_product_surface]
    routing:
      owns: the real gated in-app product workflow under `product/site/` (gated source root `src/screens/`), the product's `runtime_features` selection and customer-experience shape, plus the `product_workflow` doctrine on the canonical surface contract
      when_to_use:
        - the operator explicitly asks to build the product, app, or product backend after bootstrap, and the work is not first-pass landing/access-shell setup
        - the access shell is already in place and the operator wants to define or extend what the customer actually does after sign-in
        - "`product_workflow` is missing, incomplete, or outdated on the current surface contract"
        - a prior gated workflow build is partial and needs another bounded worker pass
      do_not_use_for:
        - first-pass landing/access-shell bootstrap work that belongs to `takyon-build-product`
        - shared auth, checkout, entitlements, usage, or other runtime rail wiring that belongs to `takyon-app-runtime`
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

# Takyon Product Workflow

## Overview

Use this skill to define and implement the real in-app product workflow after sign-in: the customer job, the closed saved-record loop, the product-specific backend leaves when needed, and the gated product UI that lives in the gated source root (see Quick Reference).

This skill is the **first and only** place the product's meaning is defined. `takyon-build-product` only seeds the scaffold and wires the honest shared shell, leaving product semantics unset (`workflow_pending`). Everything about what the product *is* is owned here and declared when the real product becomes clear, not guessed at bootstrap: the customer-experience shape (`surface_goal`, `conversion_model`, required routes/sections/tabs), the runtime rail selection (`runtime_features`), whether the product is generic or AI-backed, and the in-app `product_workflow` doctrine including `product_workflow.actions`.

## When to Use

- Use when the operator explicitly asks to build the product, app, or product backend after bootstrap and the work is not first-pass landing/access-shell setup. This is also the immediate follow-on skill when `takyon-build-product` just finished the first truthful access shell and the operator asked for the real product from scratch in the same turn.
- Use when the public/access shell exists but the real product workflow inside `/app` is missing, weak, or misleading.
- Use when the operator asks to build, change, or extend what the product actually does after sign-in.
- Use when `product_workflow` on the surface contract needs to be created, repaired, or brought back in sync with the actual gated UI.
- Do not use for landing-page/bootstrap shell work; route that to `takyon-build-product`.
- Do not use for shared backend rail wiring by itself; route that to `takyon-app-runtime`.

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/surface.md`, `product/site/`
- Canonical doctrine location: `product_workflow` on the app surface contract rendered into `product/surface.md`
- Main source target: the lane's gated source root under `product/site/`
- Gated source root: `src/screens/` (plus `src/lib/` and `src/components/`) in the scaffold-seeded static Vite SPA — the only supported lane. If the current source is still the old Next/AppKit tree under `src/app/app/(product)/`, treat that as upgrade input, not a supported target lane.
- Tool names used by this skill: `business_read_business`, `business_read_file`, `business_list_files`, `business_upsert_app_surface_contract`, `business_claude_agent_task`, `business_refresh_product_surface`

## Prerequisites

- The Takyon toolset must be available.
- Start with `business_read_business`, then load `product/surface.md` with `business_read_file` if it exists.
- Inspect the current gated source with `business_list_files` or `business_read_file` before deciding the workflow is missing.
- The access shell should already exist. If `/`, `/app`, or `/app/profile` are still the main blocker, route back to `takyon-build-product`.
- If the intended workflow needs rails that are not yet declared or live on the surface contract, record those truthfully before asking Claude to build UI on top of them.
- For this skill, treat `actions` as the default per-product backend leaf whenever the workflow needs server-side orchestration, third-party fetches, scheduled work, validation, or logic that does not fit cleanly inside the shared rails alone. On the `vite_react_ts` lane, even a plain AI transform belongs to a named action instead of a client `/generate` call. Declare `actions` on `runtime_features` and declare the named actions on `product_workflow.actions` before delegating `product/site/` work when you already know them. If the delegated worker discovers the real verb/action list only after it gets into the concrete implementation, let it write the exact contract patch to `product/site/_takyon/worker-surface-contract.json` in that same delegated run so Takyon applies the update before refresh instead of bouncing back out to CEO. For the exact field schema and runtime verification recipe, follow `takyon-app-runtime`.
- When the workflow needs re-engagement or transactional notices ("you have a new match", "someone replied"), declare the `email` rail alongside `actions` and let schedule-triggered actions send through it; the send tool, limits, and verification recipe live in `takyon-app-runtime`.

## References

- None by default. Read `research/strategy.md` and the current `product/surface.md` instead of inventing a parallel reference file.

## Templates

- None by default. Publish workflow truth onto the canonical surface contract instead of creating a parallel MVP template file.

## Scripts

- None by default.

## How to Run

- Call `business_read_business` first, then inspect `product/surface.md` and the current gated source tree under `src/screens/`.
- This is the first place to declare the product's `runtime_features` and customer-experience shape. If `takyon-build-product` left them unset (the bootstrap default), you are the first to author them; select only the rails the real workflow actually uses, and declare named `actions` only when the workflow claims real backend behavior — not earlier.
- Use `business_upsert_app_surface_contract` to record the real product workflow on the canonical surface contract under `product_workflow`; do not create a parallel MVP markdown file.
- The durable target is an MVP-complete and checkable `product_workflow`: one primary user/workspace model, one primary job, one closed saved-record loop, persistence rules, complexity bounds, first-run rules, success moment, acceptance tests, and `not_now` cuts. But do not force the CEO to front-fill every field before a substantial worker pass. Record the smallest truthful workflow doctrine that is already clear, then let the delegated worker tighten or revise it via `product/site/_takyon/worker-surface-contract.json` when the concrete implementation reveals the exact loop, persistence, or action shape. MVP-complete means **scoped-but-real**, not field-filled: one real primary user, one real primary job, and one genuinely useful end-to-end loop a real customer would value, with real persistence. A loop that saves a record nobody benefits from, a job with no real user, or placeholder/theater UI is not MVP-complete even if every field is populated; empty, false, or placeholder values must read as `workflow_pending`/blocked, never as complete.
- When the workflow needs product-specific backend behavior, default to `actions` instead of inventing client-side simulation or trying to stretch `takyon-build-product` into a backend skill. Record `actions` in `runtime_features`, declare the named action specs under `product_workflow.actions`, and let the delegated worker implement the matching `product/site/actions/<name>.ts` files plus the `invokeAction(...)` or `createActionRunner(...)` callers.
- When the gated UI work is substantial, delegate one bounded `business_claude_agent_task` call on `product/site/`.
- Design guidance is automatic for product/site work: the full design-pack set and a choose-one-direction directive are injected by default. Pass an explicit `guidance_skills` subset only to narrow the direction, or `guidance_skills: []` to opt out.
- Preserve the seeded auth/paywall/account helpers and route boundaries. Build the workflow on top of the prepared `_takyon/` substrate instead of reimplementing app-plane rails.
- Edit only the lane's gated source root unless the operator explicitly requests another route.
- Do not modify the shell/landing boundaries unless explicitly requested. On the supported lane that means `src/screens/landing.tsx`, `src/screens/app-layout.tsx`, `src/screens/profile.tsx`, and `src/screens/support.tsx`. If the source is still the old Next/AppKit tree, route that upgrade back through `takyon-build-product` instead of treating those files as a normal workflow target.
- When the source changes should be visible or published, follow the worker pass with `business_refresh_product_surface`.

## Procedure

1. Call `business_read_business` and load the current `product/surface.md` if it exists.
2. Inspect the current gated product source under `src/screens/`. If the access shell itself is still missing or broken, stop and route the work back to `takyon-build-product`.
3. Read the business research, especially `research/strategy.md`, and identify the smallest real customer job plus any already-clear workflow constraints for the gated product pass.
4. Call `business_upsert_app_surface_contract` to set or repair `product_workflow` on the canonical surface contract. Record the smallest truthful workflow doctrine you already know before delegation. If the exact loop, persistence details, or action shape only become clear during implementation, let the worker patch them in the same run through `product/site/_takyon/worker-surface-contract.json` instead of front-filling the whole doctrine.
5. If the workflow requires declared runtime rails such as `actions`, `records`, `usage`, `directory`, or `connections`, make sure those are truthful on the same surface contract before delegating UI work. For this skill, default to adding `actions` whenever the workflow needs any per-product backend leaf beyond the shared rails.
6. When `actions` is needed and the real names are already clear, record the named action specs under `product_workflow.actions` before delegation so the worker receives the action contract and can implement the real backend leaf under `product/site/actions/` instead of faking it in the client. If the worker discovers it needs a new action name or another contract correction mid-build, it should update `product/site/_takyon/worker-surface-contract.json` in that same delegated run; Takyon applies that patch before refresh rather than kicking the job back out for a second CEO/worker bounce.
7. Delegate one bounded `business_claude_agent_task` call on `product/site/` with instructions scoped to the gated workflow. Tell the worker which lane the product is on and to edit only the lane's gated source root unless the operator explicitly requests another route, and tell it to implement or wire the declared product actions when they are part of the workflow. If the concrete build proves the workflow doctrine or action contract needs to change, let the worker patch it in the same run and continue. The full design-pack set and a choose-one-direction directive are injected by default, so you do not need to pass `guidance_skills` for ordinary design work.
8. Review the changed source. Reject the pass if it modifies shell/landing boundary files without an explicit operator request: `src/screens/landing.tsx`, `src/screens/app-layout.tsx`, `src/screens/profile.tsx`, or `src/screens/support.tsx`. If the source still lives under the old Next/AppKit tree, stop and route the upgrade back through `takyon-build-product`.
9. If the workflow source should be visible or published, call `business_refresh_product_surface` and treat its blocker output as truth.
10. Before claiming success, re-read the updated `product/surface.md` and the touched gated source files so the report reflects the exact durable state.

## Output Format

- `product/surface.md` should truthfully record the current `product_workflow`, runtime dependencies, what works now, and what is still blocked.
- `product/site/` should contain real gated source for the workflow, not notes or fake placeholders.

## Publication

- Publish the product-workflow doctrine through the canonical surface contract rendered at `product/surface.md`.
- Publish the actual gated source under `product/site/`, primarily the lane's gated source root.
- Any claimed public or local publish state must still be backed by `business_refresh_product_surface` output or receipts.

## Common Pitfalls

- Writing a nice gated UI without recording the workflow doctrine on the surface contract
- Faking persistence or history when the required runtime rail is not declared or not live
- Forgetting to declare `actions` and `product_workflow.actions` before delegating a workflow that plainly needs product-specific backend logic
- Letting this skill sprawl into landing-page/bootstrap shell work that belongs to `takyon-build-product`
- Letting the worker invent backend behavior or shared rails instead of routing that work through `takyon-app-runtime`

## Verification Checklist

- [ ] `product_workflow` is present and truthful on the canonical surface contract
- [ ] The workflow is scoped-but-real: a real user, a real job, and a genuinely useful loop — no placeholder records, fake feeds, or theater UI; empty/false fields are reported as `workflow_pending`, not as a complete workflow
- [ ] The gated source lives under `src/screens/` unless another route was explicitly requested
- [ ] Any required runtime rail is declared truthfully before the UI claims it works
- [ ] Any workflow that needs product-specific backend logic declares `actions` plus the named `product_workflow.actions` before the UI or source claims the backend behavior is real
- [ ] Any claimed publish or preview state is backed by `business_refresh_product_surface` output or receipts
- [ ] No parallel MVP spec file was created

## Rules

1. Do not create a second MVP doc when the canonical surface contract can hold the workflow truth.
2. Do not fake saved state, records, matching, generation, or any other runtime-backed capability.
3. Keep the real product workflow business-scoped and gated.
4. Route shared backend rail changes through `takyon-app-runtime`.
5. For this skill, default to `actions` for per-product backend leaves instead of client-side simulation or stretching bootstrap-only skills into backend ownership.
6. For design-heavy gated workflow work, let Claude choose one coherent visual direction from the provided style packs; do not hardcode a single style pack choice in this skill.
7. Do not let this skill reskin the landing page, support pages, access gate, or account page unless the operator explicitly requests that broader scope.
8. When a canonical tool in this flow is blocked — `business_upsert_app_surface_contract` fails, `business_claude_agent_task` reports the worker runtime unavailable, or `business_refresh_product_surface` cannot run — record the exact blocker on the business and stop. Do not hand-write `product/site/` source in-loop as a substitute for the delegated worker, do not hand-author `surface.md` as a substitute for the contract tool, and do not leave a built surface whose refresh gate never ran: an unreviewed hand-built product is how rail-shadowing shims ship.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `/app` shell is still the blocker | Route back to `takyon-build-product` before trying to add the deeper workflow |
| The workflow needs a rail that is missing or blocked | Record that truth on the surface contract and route the backend work through `takyon-app-runtime` |
| The workflow obviously needs product-specific backend logic | Default to declaring `actions` plus named `product_workflow.actions`, then delegate `product/site/` work so the worker gets the actions contract |
| The worker returns `BLOCKED:` | Stop the same-turn source loop, record the blocker, and keep `product/surface.md` truthful |
