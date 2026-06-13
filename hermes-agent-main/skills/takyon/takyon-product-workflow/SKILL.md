---
name: takyon-product-workflow
description: Build or evolve the real gated in-app workflow under `/app`.
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
    requires_tools: [business_read_business, business_claude_agent_task, business_refresh_product_surface]
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

Use this skill after bootstrap when `/app` still exists only as the shell and needs to become the real product.

This skill should inspect the existing source and flesh out the actual product workflow under `/app`. It does **not** need a second contract, action declarations, or a doctrine checklist. If the product needs backend behavior, create real `product/site/actions/<name>.ts` files and call them from the UI.

## When to Use

- Use when `/` already exists and `/app` is still only the shell.
- Use when the operator asks to build the real product experience after sign-in.
- Use when `/app` is weak, starter-shaped, or missing its real customer job.
- Do not use this skill for first-pass landing/shell bootstrap; use `takyon-build-product` first.

## Quick Reference

- Primary root: `product/`
- Main source targets:
  - `src/screens/app-home.tsx`
  - `src/screens/app-layout.tsx`
  - `src/screens/profile.tsx`
  - `product/site/actions/*.ts`
- Preserve the Vite route skeleton unless the operator explicitly changes routes.

## How to Run

- Call `business_read_business` first.
- Inspect the current `product/site/` source before deciding what is missing.
- Read `research/strategy.md` and any other obviously relevant research files.
- Decide what the real post-sign-in product job is by looking at the business and the existing source, not by front-filling doctrine.
- Delegate one bounded `business_claude_agent_task` on `product/site/`.
- Tell the worker to:
  - inspect the current `/app` shell
  - turn `/app` into the real product
  - keep auth/account/subscription wiring through the shared runtime client
  - add action files under `product/site/actions/` whenever backend behavior is needed
  - use `createActionRunner(name)` / `invokeAction(name)` from the UI
  - keep landing/support/profile boundaries unless explicitly asked to change them
- Follow with `business_refresh_product_surface`.

## Procedure

1. Read the business summary and inspect the current source tree.
2. Confirm that bootstrap already produced `/` plus the `/app` shell.
3. Read research so the real product loop is grounded in the business, not invented.
4. Delegate one `business_claude_agent_task` on `product/site/` to flesh out `/app`.
5. If backend behavior is needed, let the worker create real `product/site/actions/<name>.ts` files and wire them from the UI.
6. Refresh/publish with `business_refresh_product_surface`.

## Output Format

- `product/site/` contains a real product workflow under `/app`
- `product/site/actions/` contains any needed per-product backend files
- `product/surface.md` reflects the current routes/source/publish state

## Rules

1. Build the real product by inspecting the actual source and business context, not by filling out doctrine fields.
2. Do not require action declaration before using real action files.
3. Keep auth/account/subscription behavior on the shared runtime rails.
4. Keep landing/support/profile boundaries unless explicitly asked to broaden scope.
5. Do not fake saved state or backend behavior in the browser when an action file should exist.
