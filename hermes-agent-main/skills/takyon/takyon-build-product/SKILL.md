---
name: takyon-build-product
description: Create or improve the smallest credible product, site, or offer surface for one Takyon business and write it into canonical product files.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, product, website, app, offer]
    related_skills: [takyon-market-research, takyon-app-runtime, takyon-distribution, takyon-claude-agent-sdk]
    requires_toolsets: [takyon]
    requires_tools: [business_read_business, business_upsert_app_surface_contract, business_claude_agent_task, business_refresh_product_surface]
  takyon:
    scope: business
    allowed_roots: [product, metrics]
    output_root: product
    publication:
      - product/design-brief.md
      - product/surface.md
      - product/site
required_environment_variables: []
required_credential_files: []
---

# Takyon Build Product

## Overview

Use this skill to create or materially improve the business-owned product surface: the offer, the website, the app route, the source path, and the honest public claims around what works now.

## When to Use

- Use after research when the business needs a real product or site surface.
- Use when the current source is too weak, misleading, unpublished, or mismatched to the offer.
- Use when the operator asks to build, publish, or repair the business product surface.
- Do not use for auth, entitlements, billing, or checkout wiring by itself; route that work through `takyon-app-runtime`.

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/design-brief.md`, `product/surface.md`, `product/site/`
- Best call points: post-research product creation, surface repair, honest publication work
- Publication lane: publish from `product/site/`; reflect refreshed state in `product/surface.md`
- Tool names used by this skill: `business_read_business`, `business_read_file`, `business_list_files`, `business_check_runtime_capabilities`, `business_upsert_app_surface_contract`, `business_create_workspace`, `business_write_file`, `business_patch_file`, `business_claude_agent_task`, `business_refresh_product_surface`

## Prerequisites

- The Takyon toolset must be available.
- Start with canonical business state: `business_read_business`, then load `product/design-brief.md` and `product/surface.md` with `business_read_file` if they already exist.
- If the source path, framework, or local toolchain is unclear, use `business_check_runtime_capabilities` before acting as if a build stack already exists.
- If the business has no surface contract yet, create it first with `business_upsert_app_surface_contract`.
- For substantial `product/site/` work, prefer one delegated `business_claude_agent_task` pass and let that worker finish the source itself. Do not default to CEO source inspection, local hand-patching, or a second delegated pass in the same turn unless the worker explicitly returns `BLOCKED:` or the operator asked for manual repair.
- During bootstrap for a software business, establish a real `product/site/` source path before creating extra runtime notes or billing prose.

## References

- `references/surface-rules.md`

## Templates

- `templates/design-brief.md`
- `templates/surface.md`

## How to Run

- Call `business_read_business` first to inspect the current business summary, product surface, and publication state.
- Use `business_read_file` and `business_list_files` to inspect `product/design-brief.md`, `product/surface.md`, and the current source tree before rewriting anything.
- Use `business_upsert_app_surface_contract` to set or repair the canonical source path, routes, runtime_features, the CEO-owned customer experience shape, publish target, and done gate.
- Set the subuser app shape on that same surface contract before delegation when the answer is known: `app_mode`, `subscription_style`, `api_mode`, and any per-rail `rail_state`.
- Before delegation, read `research/` broadly, especially `research/strategy.md`, then write the chosen customer experience shape onto the same surface contract: `surface_goal`, `conversion_model`, `required_routes`, `required_sections`, `required_app_tabs`, `research_sources`, and any `experience_notes`.
- Use `business_create_workspace`, `business_write_file`, and `business_patch_file` for tiny local product-file edits, especially briefs, receipts, and small source fixes under `product/`.
- Use `business_claude_agent_task` only when the delegated worker lane is available and the job is meaningfully larger than a direct first-surface build. When visual quality matters, pass `guidance_skills: ["claude-design", "<style-skill>"]`.
- For `product/site/`, delegate one bounded source pass and let the worker own the source/build loop. The worker will receive the prepared shared subuser app kit under `product/site/_takyon/`; build the business-specific UI around that substrate instead of reinventing app-plane rails.
- When first publish speed matters, prefer the smallest honest, dependency-light source that can verify and publish quickly. Do not default to a heavy framework if a static or minimal app shell is enough for the first truthful surface.
- Use `business_refresh_product_surface` only when there is real source to publish. Treat its blocker output and receipt as truth.
- During bootstrap, once `product/site/` exists with real source, complete one honest refresh/publish pass before later auth/customer/outreach follow-on work.

## Procedure

1. Call `business_read_business` and identify the current offer, source path, publish target, and blocker state. If `product/design-brief.md` or `product/surface.md` exist, load them with `business_read_file`.
2. If there is no canonical surface contract or the source path is wrong, call `business_upsert_app_surface_contract` first. Default to a real source path under `product/site/`; do not invent a publish target without recording it.
3. If the product will claim runtime-backed behavior such as auth, checkout, billing, usage, entitlements, account, or generate, record those as `runtime_features` on the surface contract before delegating source work.
4. Read `research/` broadly before deciding the customer surface, and explicitly inspect `research/strategy.md` by name plus any other relevant files in `research/`. Use that evidence to choose the customer-maximizing surface shape.
5. Record that customer experience shape on the surface contract before delegation: `surface_goal`, `conversion_model`, `required_routes`, `required_sections`, `required_app_tabs`, `research_sources`, and any `experience_notes`. This is the CEO-owned product decision that Claude should implement rather than rediscovering from scratch.
6. When the app shape is known, set `app_mode`, `subscription_style`, and `api_mode` on that same surface contract, plus any truthful per-rail `rail_state` values such as `live`, `blocked`, `broken`, or `unknown`.
7. If `product/site/` or the expected source directory does not exist, create it with `business_create_workspace` and write the initial structure there before claiming product work is underway.
8. If the local runtime, package manager, or framework capability is unclear, call `business_check_runtime_capabilities` and only proceed with the stack the runtime actually supports.
9. Write or patch `product/design-brief.md` so it names the audience, offer, routes, constraints, and what evidence this product surface is supposed to create next.
10. During bootstrap for software businesses, default the first surface mode to `app_shell`, not `landing_page_only`. Only choose landing-only when the operator or current evidence explicitly calls for a validation/offer-page-first surface.
11. Write or patch `product/surface.md` so it records the truthful current state: source path, routes, runtime_features, customer experience shape, app shape, what works now, what is blocked, and what still depends on app-runtime or provider work. That same contract is what refreshes the prepared `_takyon/` kit and what the worker receives as product-site UI truth.
12. Decide whether the source work fits inside one CEO turn. For a substantial first honest site/app publish, default to one delegated `business_claude_agent_task` pass under `product/site/` so the worker can finish the source, local build/test, and cleanup inside its isolated lane. If that worker explicitly returns `BLOCKED:` or hits a provider/runtime gate, record the blocker instead of defaulting to CEO source repair in the same turn.
13. If the source work is design-heavy or outward-facing, choose one shared style skill and include `guidance_skills: ["claude-design", "<style-skill>"]` in the worker call so the Claude Agent SDK worker receives both the distilled design method and one coherent shared design system without changing the canonical ownership path.
14. If the operator asked for publication and the source is real, call `business_refresh_product_surface` before later auth/customer/outreach follow-on work. If it returns a blocker, write that blocker back into `product/surface.md` and stop the same-turn source loop instead of defaulting to CEO repair passes.
15. During bootstrap, treat `product/site/` plus a truthful `product/surface.md` source path as the product completion threshold before letting extra runtime notes become the main visible outcome. Once the first honest public surface is live, continue the rest of bootstrap in the same turn.

### Shared style skills

Choose exactly one style skill for outward-facing `product/site/` work:

- `claude-design-openai`: calm serious default for AI tools, research, prosumer, and productivity surfaces
- `claude-design-stripe`: premium B2B, commercial, fintech, infra
- `claude-design-superhuman`: premium productivity, focus, speed
- `claude-design-vibrant`: fun consumer, creator, colorful prosumer
- `claude-design-doodle`: whimsical playful consumer, pets, kids, deliberately silly products

Default to `claude-design-openai` unless the product clearly wants a different tone.

## Output Format

- `product/design-brief.md` should describe audience, offer, routes, and constraints.
- `product/surface.md` should describe what is actually wired, what is blocked, and which `runtime_features` the app-runtime lane must reconcile.
- `product/site/` should contain real source, not placeholder notes pretending to be source.

## Publication

- The canonical local source path is `product/site/`.
- Publish the design brief to `product/design-brief.md`.
- Publish the surface contract and current state to `product/surface.md`.
- Public or local publication state must be reflected honestly in `product/surface.md`.
- Do not claim a deployed URL, working route, or finished publish unless the runtime or tool receipts actually support it.

## Common Pitfalls

- Writing polished copy while leaving the real source or route missing
- Claiming runtime-backed product behavior that only exists in prose
- Splitting the actual product surface across random directories
- Treating `business_claude_agent_task` as a second owner instead of the implementation lane underneath this skill
- Claiming publication state without an explicit `business_refresh_product_surface` result or visible receipt
- Falling back to CEO hand-patching right after a delegated `product/site/` worker pass instead of letting the worker finish or block cleanly

## Verification Checklist

- [ ] `product/design-brief.md` and `product/surface.md` agree on the offer and current state
- [ ] `product/site/` contains usable source, not only notes
- [ ] Any claimed publication state is backed by `business_refresh_product_surface` output or visible receipts
- [ ] Any runtime-dependent feature is either routed to `takyon-app-runtime` or left visibly blocked
- [ ] Bootstrap did not substitute extra runtime notes for missing `product/site/` source

## Rules

1. Do not fake auth, billing, sessions, checkout, usage, or provider-backed features.
2. Use real source files when the operator asked for a built surface.
3. Keep the surface business-owned and honest.
4. Use app-runtime rails when the product needs shared backend behavior.
5. For substantial `product/site/` implementation, prefer one delegated `business_claude_agent_task` pass. Do not default to CEO source inspection or a second delegated pass in the same turn unless the worker explicitly returns `BLOCKED:` or the operator asked for manual repair.
6. For software businesses, bootstrap should default to a real app shell. Do not quietly substitute a landing-only waitlist page unless the operator or evidence explicitly calls for landing-page-only validation mode.
7. In customer-facing design and copy, default to capability language instead of vendor/model labels. If the operator explicitly wants named model positioning, use current names accurately; do not leak stale labels like `GPT-4o-mini` into the product surface.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Publish is blocked | Record the exact blocker and keep the local product surface honest |
| The product needs backend behavior | Route through `takyon-app-runtime` before inventing custom state |
