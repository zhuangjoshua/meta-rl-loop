---
name: takyon-build-product
description: "Bootstrap one Takyon business product site: build the landing page and the auth/subscription shell."
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
    requires_tools: [business_read_business, business_upsert_app_surface_contract, business_upsert_app_plan, business_claude_agent_task, business_refresh_product_surface]
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

Use this skill to do the first real product build:

- build the public landing page at `/`
- keep the Vite SPA route skeleton
- leave `/app` as the truthful auth/subscription/account shell
- leave the deeper post-sign-in product for `takyon-product-workflow`

This skill does **not** define the whole product workflow. It gets the site and shell real enough that a customer can understand the offer and enter the gated app cleanly.

## When to Use

- Use after research when the business needs its first real customer surface.
- Use when `/` is missing, weak, or still starter-shaped.
- Use when `/app` and `/app/profile` need to exist as the honest auth/subscription shell.
- Do not use this skill to flesh out the deep product loop inside `/app`; use `takyon-product-workflow` for that.

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/surface.md`, `product/site/`
- Supported frontend lane: pinned Vite SPA only
- Default route skeleton to preserve unless explicitly changed:
  - `/`
  - `/faq`
  - `/privacy`
  - `/terms`
  - `/articles`
  - `/app`
  - `/app/profile`

## How to Run

- Call `business_read_business` first.
- Read `research/strategy.md` and any obviously relevant research files.
- Call `business_upsert_app_surface_contract` only to keep the single surface contract truthful:
  - `source_path`
  - `routes`
  - `publish_target`
  - `notes`
- Ensure the canonical monthly plan exists with `business_upsert_app_plan` before claiming paid subscription UX is real.
- Delegate one bounded `business_claude_agent_task` on `product/site/`.
- Tell the worker to:
  - build the landing page at `/`
  - keep `/app` as the auth/subscription shell
  - keep `/app/profile` as the account/subscription page
  - keep access/account decisions on the shared helpers in `src/lib/hooks.ts`
  - treat runtime account truth as `user` plus `entitlements[]`
  - not hand-roll subscription gates from legacy fields like `has_active_subscription`, nested `subscription.status`, or bespoke `client.account()` parsing in the screens
  - preserve the Vite route skeleton
  - not try to finish the full product workflow inside `/app`
- Follow with `business_refresh_product_surface`.
- If the operator asked for the full product from scratch, continue into `takyon-product-workflow` after this shell is real.

## Procedure

1. Read the business summary and current product state.
2. Read research, especially `research/strategy.md`, so the landing page is grounded in the actual offer.
3. Upsert the one surface contract with:
   - `source_path=product/site`
   - the canonical route skeleton
   - the publish target
   - truthful notes
4. Ensure the monthly app plan exists before claiming paid subscription flow is live.
5. Delegate one `business_claude_agent_task` run on `product/site/` to build the landing page and shell.
6. Refresh/publish with `business_refresh_product_surface`.
7. If the operator wanted the real product, continue straight into `takyon-product-workflow`.

## Output Format

- `product/site/` contains a real landing page and truthful `/app` shell
- `product/surface.md` reflects the current source path, routes, publish state, and notes

## Rules

1. Build a real landing page at `/`; do not leave a redirect or fake placeholder.
2. Keep `/app` and `/app/profile` as the truthful auth/subscription/account shell.
3. Keep the Vite SPA route skeleton unless the operator explicitly changes routes.
4. Use the shared access/account helpers in `src/lib/hooks.ts` for `/app` and `/app/profile`; treat `user` plus `entitlements[]` as the canonical subscription truth.
5. Do not reintroduce legacy subscription parsing such as `has_active_subscription`, nested `subscription.status`, or ad hoc screen-local `client.account()` adapters.
6. Do not invent deep product workflow doctrine here.
7. Do not probe actions or fake the post-sign-in product loop here.
8. Do not ship visible starter/scaffold UI as the product.
