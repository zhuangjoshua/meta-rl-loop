---
name: takyon-product-workflow
description: Build or evolve the real gated in-app product workflow inside `product/site/src/app/app/(product)/` and record that workflow on the canonical surface contract.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, product, workflow, app, mvp]
    related_skills: [takyon-build-product, takyon-app-runtime, takyon-market-research, takyon-claude-agent-sdk]
    requires_toolsets: [takyon]
    requires_tools: [business_read_business, business_upsert_app_surface_contract, business_claude_agent_task, business_refresh_product_surface]
    routing:
      owns: the real gated in-app product workflow inside `product/site/src/app/app/(product)/` plus the `product_workflow` doctrine on the canonical surface contract
      when_to_use:
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

Use this skill to define and implement the real in-app product workflow after sign-in: the customer job, the closed saved-record loop, and the gated product UI that lives inside `src/app/app/(product)/`.

## When to Use

- Use when the public/access shell exists but the real product workflow inside `/app` is missing, weak, or misleading.
- Use when the operator asks to build, change, or extend what the product actually does after sign-in.
- Use when `product_workflow` on the surface contract needs to be created, repaired, or brought back in sync with the actual gated UI.
- Do not use for landing-page/bootstrap shell work; route that to `takyon-build-product`.
- Do not use for shared backend rail wiring by itself; route that to `takyon-app-runtime`.

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/surface.md`, `product/site/`
- Canonical doctrine location: `product_workflow` on the app surface contract rendered into `product/surface.md`
- Main source target: `product/site/src/app/app/(product)/`
- Tool names used by this skill: `business_read_business`, `business_read_file`, `business_list_files`, `business_upsert_app_surface_contract`, `business_claude_agent_task`, `business_refresh_product_surface`

## Prerequisites

- The Takyon toolset must be available.
- Start with `business_read_business`, then load `product/surface.md` with `business_read_file` if it exists.
- Inspect the current gated source with `business_list_files` or `business_read_file` before deciding the workflow is missing.
- The access shell should already exist. If `/`, `/app`, or `/app/profile` are still the main blocker, route back to `takyon-build-product`.
- If the intended workflow needs rails that are not yet declared or live on the surface contract, record those truthfully before asking Claude to build UI on top of them.

## References

- None by default. Read `research/strategy.md` and the current `product/surface.md` instead of inventing a parallel reference file.

## Templates

- None by default. Publish workflow truth onto the canonical surface contract instead of creating a parallel MVP template file.

## Scripts

- None by default.

## How to Run

- Call `business_read_business` first, then inspect `product/surface.md` and the current `product/site/src/app/app/(product)/` source tree.
- Use `business_upsert_app_surface_contract` to record the real product workflow on the canonical surface contract under `product_workflow`; do not create a parallel MVP markdown file.
- Keep `product_workflow` MVP-complete and checkable: one primary user/workspace model, one primary job, one closed saved-record loop, persistence rules, complexity bounds, first-run rules, success moment, acceptance tests, and `not_now` cuts.
- When the gated UI work is substantial, delegate one bounded `business_claude_agent_task` call on `product/site/`.
- For design-heavy gated workflow work, pass `guidance_skills: ["claude-design", "claude-design-openai", "claude-design-stripe", "claude-design-superhuman", "claude-design-vibrant", "claude-design-doodle"]` and tell Claude to choose one coherent visual direction from the brief and follow it consistently without blending packs.
- Preserve the seeded auth/paywall/account helpers and route boundaries. Build the workflow on top of the prepared `_takyon/` substrate instead of reimplementing app-plane rails.
- Edit only `src/app/app/(product)/**` unless the operator explicitly requests another route.
- Do not modify `src/app/page.js`, `src/app/privacy/page.js`, `src/app/terms/page.js`, `src/app/faq/page.js`, `src/app/articles/page.js`, `src/app/app/page.js`, or `src/app/app/profile/page.js` unless explicitly requested.
- When the source changes should be visible or published, follow the worker pass with `business_refresh_product_surface`.

## Procedure

1. Call `business_read_business` and load the current `product/surface.md` if it exists.
2. Inspect the current gated product source under `product/site/src/app/app/(product)/`. If the access shell itself is still missing or broken, stop and route the work back to `takyon-build-product`.
3. Read the business research, especially `research/strategy.md`, and decide the real customer job and closed loop the product should support after sign-in.
4. Call `business_upsert_app_surface_contract` to set or repair `product_workflow` on the canonical surface contract. Keep it concrete and bounded:
   - one primary user/workspace model
   - one primary job sentence
   - one closed loop that includes saved state
   - truthful persistence rules and runtime dependencies
   - a bounded product budget
   - first-run/empty/error expectations
   - concrete acceptance tests
   - `not_now` cuts
5. If the workflow requires declared runtime rails such as `records`, `generate`, `usage`, `directory`, or `connections`, make sure those are truthful on the same surface contract before delegating UI work.
6. Delegate one bounded `business_claude_agent_task` call on `product/site/` with instructions scoped to the gated workflow. Tell the worker to edit only `src/app/app/(product)/**` unless the operator explicitly requests another route. For design-heavy work, pass the full design-pack set with `claude-design` and tell Claude to choose one coherent visual direction from the brief, then follow it consistently.
7. Review the changed source. Reject the pass if it modifies `src/app/page.js`, `src/app/privacy/page.js`, `src/app/terms/page.js`, `src/app/faq/page.js`, `src/app/articles/page.js`, `src/app/app/page.js`, or `src/app/app/profile/page.js` without an explicit operator request.
8. If the workflow source should be visible or published, call `business_refresh_product_surface` and treat its blocker output as truth.
9. Before claiming success, re-read the updated `product/surface.md` and the touched gated source files so the report reflects the exact durable state.

## Output Format

- `product/surface.md` should truthfully record the current `product_workflow`, runtime dependencies, what works now, and what is still blocked.
- `product/site/` should contain real gated source for the workflow, not notes or fake placeholders.

## Publication

- Publish the product-workflow doctrine through the canonical surface contract rendered at `product/surface.md`.
- Publish the actual gated source under `product/site/`, primarily `src/app/app/(product)/`.
- Any claimed public or local publish state must still be backed by `business_refresh_product_surface` output or receipts.

## Common Pitfalls

- Writing a nice gated UI without recording the workflow doctrine on the surface contract
- Faking persistence or history when the required runtime rail is not declared or not live
- Letting this skill sprawl into landing-page/bootstrap shell work that belongs to `takyon-build-product`
- Letting the worker invent backend behavior or shared rails instead of routing that work through `takyon-app-runtime`

## Verification Checklist

- [ ] `product_workflow` is present and truthful on the canonical surface contract
- [ ] The gated source lives under `product/site/src/app/app/(product)/` unless another route was explicitly requested
- [ ] Any required runtime rail is declared truthfully before the UI claims it works
- [ ] Any claimed publish or preview state is backed by `business_refresh_product_surface` output or receipts
- [ ] No parallel MVP spec file was created

## Rules

1. Do not create a second MVP doc when the canonical surface contract can hold the workflow truth.
2. Do not fake saved state, records, matching, generation, or any other runtime-backed capability.
3. Keep the real product workflow business-scoped and gated.
4. Route shared backend rail changes through `takyon-app-runtime`.
5. For design-heavy gated workflow work, let Claude choose one coherent visual direction from the provided style packs; do not hardcode a single style pack choice in this skill.
6. Do not let this skill reskin the landing page, support pages, access gate, or account page unless the operator explicitly requests that broader scope.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `/app` shell is still the blocker | Route back to `takyon-build-product` before trying to add the deeper workflow |
| The workflow needs a rail that is missing or blocked | Record that truth on the surface contract and route the backend work through `takyon-app-runtime` |
| The worker returns `BLOCKED:` | Stop the same-turn source loop, record the blocker, and keep `product/surface.md` truthful |
