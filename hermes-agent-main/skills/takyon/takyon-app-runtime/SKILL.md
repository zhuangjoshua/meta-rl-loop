---
name: takyon-app-runtime
description: Configure canonical auth, profile, entitlements, checkout, billing, usage, and runtime wiring for one Takyon business app without inventing backend state.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, app-runtime, auth, profile, checkout, billing, entitlements]
    related_skills: [takyon-build-product, takyon-business-metrics]
    requires_toolsets: [takyon]
    requires_tools: [business_read_business, business_upsert_app_plan, business_request_app_magic_link, business_create_app_checkout]
    routing:
      owns: auth, sessions, checkout, entitlements, billing, usage, and runtime wiring for the business app
      when_to_use:
        - real app customer state, pricing, checkout, or entitlements must be wired honestly
        - usage tracking or app budget gates materially affect the product
      do_not_use_for:
        - pure page or layout work unless the runtime contract itself is changing
  takyon:
    scope: business
    allowed_roots: [product, metrics]
    output_root: product
    publication:
      - product/surface.md
required_environment_variables: []
required_credential_files: []
---

# Takyon App Runtime

## Overview

Use this skill when the business app needs real customer auth, sessions, profiles, plans, entitlements, checkout, subscription reconciliation, revenue, or usage tracking.

## When to Use

- Use when a product needs real app customer state, not mock UI state.
- Use when pricing, checkout, or entitlement behavior must be wired honestly.
- Use when usage tracking or app budget gates matter.
- Do not use for pure page/layout work unless the runtime contract itself is changing.

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/surface.md` plus any explicitly requested runtime notes
- Best call points: auth, profile, billing, entitlements, checkout, usage wiring, AI generation gateway
- Publication location: `product/surface.md` by default
- Tool names used by this skill: `business_read_business`, `business_read_file`, `business_upsert_app_surface_contract`, `business_configure_app_budget`, `business_upsert_app_plan`, `business_upsert_app_customer`, `business_upsert_app_profile`, `business_grant_app_entitlement`, `business_request_app_magic_link`, `business_verify_app_magic_link`, `business_read_app_account`, `business_read_app_profile`, `business_create_app_checkout`, `business_record_stripe_webhook`, `business_record_app_usage`, `business_write_file`, `business_patch_file`

## Prerequisites

- The Takyon toolset must be available.
- Start with `business_read_business`, then inspect `product/surface.md` and any existing runtime notes with `business_read_file`.
- Keep top-level Takyon users and product customers separate; this skill only manages the product-customer side.
- If live auth, checkout, email, or billing depends on provider credentials, let the runtime tools expose the blocker instead of pretending those providers are already configured.
- If `product/surface.md` has no real `source_path` or `product/site/` does not exist yet, route software-business bootstrap work back through `takyon-build-product` before expanding runtime mirror docs.

## References

- `references/runtime-rules.md`

## Templates

- `templates/runtime.md` when an explicit runtime note is requested

## How to Run

- Call `business_read_business` first, then load `product/surface.md` and any existing runtime notes with `business_read_file` if they already exist.
- Treat the `Runtime Rails` section in `product/surface.md` and the selected `runtime_features` as the per-business runtime rail selector for this skill.
- Treat the same surface contract as the source of truth for the current subuser app shape too: `app_mode`, `subscription_style`, `api_mode`, and per-rail `rail_state`.
- Use `business_upsert_app_surface_contract` if the app surface contract is missing routes, source path, runtime_features, or truthful runtime notes.
- Use `business_configure_app_budget` and `business_upsert_app_plan` for plan policy, usage caps, and pricing metadata.
- Use `business_upsert_app_customer`, `business_upsert_app_profile`, `business_grant_app_entitlement`, `business_request_app_magic_link`, `business_verify_app_magic_link`, `business_read_app_account`, and `business_read_app_profile` for customer, profile, session, and entitlement flows.
- Use `business_create_app_checkout` and `business_record_stripe_webhook` for paid checkout and reconciliation. On a paid event, reconciliation also accrues the gross minus the platform application fee into the business owner's custody balance (flow B); report that as owed/accrued, not paid out.
- Use `business_record_app_usage` for real usage metering.
- For product AI generation, the generated app calls the canonical runtime route (`POST /generate` on product hosts, or `POST /api/takyon/apps/<slug>/generate` off-host). That public route brokers server-side through the shared Takyon AI authority, which meters spend against the same budget set by `business_configure_app_budget`. Select it by including `generate` in the surface contract `runtime_features`.
- The prepared `_takyon/` kit inside `product/site/` is a shared substrate, not a claim that every rail is live. Keep `rail_state` truthful so the kit and delegated worker see the same runtime reality.
- After tool-backed changes, update `product/surface.md` so the visible contract matches the real runtime state. Only write extra runtime markdown when the operator explicitly wants it.

## Procedure

1. Call `business_read_business` and identify which runtime lane is actually changing: plan policy, auth/session, customer state, profile state, entitlement, checkout, billing, or usage.
2. Read `runtime_features` from the app surface contract and treat that list as the source of truth for which runtime-backed claims this product shell expects now.
3. If the product surface currently claims runtime features that are not wired, update the surface contract first with `business_upsert_app_surface_contract` or route the product copy repair back through `takyon-build-product`.
4. During bootstrap for a software business, do not let these runtime mirror files become the first main product artifact. If `product/site/` is still missing or `product/surface.md` has no real source path, route back to `takyon-build-product` first unless runtime-first work was explicitly requested.
5. For plan and budget work, call `business_configure_app_budget` and `business_upsert_app_plan` before editing any notes. Expect those tools to define the real limits and pricing state.
6. For customer/profile/auth work, use `business_upsert_app_customer`, `business_upsert_app_profile`, `business_grant_app_entitlement`, `business_request_app_magic_link`, `business_verify_app_magic_link`, `business_read_app_account`, and `business_read_app_profile` in the order needed by the flow. If a provider or credential is missing, keep the blocker visible instead of faking a session.
7. For paid flows, call `business_create_app_checkout` to create the checkout intent and `business_record_stripe_webhook` when real Stripe events arrive. Do not claim paid entitlement or revenue until webhook reconciliation has happened. On a paid `checkout.session.completed`, that reconciliation does three things in one atomic step: records the revenue event, grants the paying sub-user's entitlement, AND accrues the gross minus the platform application fee (`STRIPE_CONNECT_APPLICATION_FEE_BPS`, default 2000 bps = 20%) into the business owner's custody balance (flow B — the sub-user→owner custody rail, distinct from the top-level Takyon user's billing ledger). Payout of that custody balance to the owner is deferred (no Stripe Connect transfer is performed yet), so reflect it truthfully in `product/surface.md` or an explicitly requested billing note, never as money already paid out.
8. For usage metering, call `business_record_app_usage`. Product AI generation is metered through the canonical runtime route (`POST /generate` on product hosts, or `POST /api/takyon/apps/<slug>/generate` off-host): that route brokers server-side through the shared Takyon AI authority and reserves then settles against the budget set by `business_configure_app_budget`. Until a dedicated usage read rail is finished on the app plane, treat `business_read_app_account` as the current read source for usage summary in the product shell. Surface `402` (over budget) and `503` (generation not configured) honestly, and never embed the platform provider key in the app or call internal authority endpoints directly from product code.
9. After real tool-backed changes, write or patch `product/surface.md` so it reflects actual runtime state and blockers, including truthful `rail_state` updates when known. That same contract refreshes the shared `_takyon/` kit inside `product/site/`.
10. If a feature is blocked by credentials, providers, or missing runtime setup, leave that path explicitly blocked in `product/surface.md` instead of inventing success.

## Output Format

- `product/surface.md` should summarize the real shared runtime contract, selected rails, and visible blockers.

## Publication

- Publish the runtime overview and blockers to `product/surface.md`.
- Any live provider-backed behavior must come from real app-runtime rails and receipts, not from these documents alone.

## Common Pitfalls

- Letting UI claims run ahead of actual runtime wiring
- Mixing top-level Takyon users with product customers
- Recording aspirational provider state as if it already exists
- Embedding the platform provider key in the generated app or bypassing the shared generate rail (`/generate` on product hosts, or `/api/takyon/apps/<slug>/generate` off-host)

## Verification Checklist

- [ ] `product/surface.md` matches the actual configured runtime behavior
- [ ] Any blocked provider path is named explicitly instead of being hidden
- [ ] No product customer, entitlement, checkout, or billing claim appears without the corresponding runtime tool truth

## Rules

1. Do not emulate auth, sessions, checkout, billing, or subscriptions in browser-only state.
2. Treat top-level Takyon users and product customers as different scopes.
3. Record only actual runtime state or explicit blockers.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Missing provider credentials | Record the exact gate and leave the surface blocked |
| Product UI claims runtime features that are not real | Repair the UI or mark the feature blocked |
