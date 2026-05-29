---
name: takyon-app-runtime
description: Configure canonical auth, entitlements, checkout, billing, usage, and runtime wiring for one Takyon business app without inventing backend state.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, app-runtime, auth, checkout, billing, entitlements]
    related_skills: [takyon-build-product, takyon-business-metrics]
    requires_toolsets: [takyon]
    requires_tools: [business_read_business, business_upsert_app_plan, business_request_app_magic_link, business_create_app_checkout]
  takyon:
    scope: business
    allowed_roots: [product, metrics]
    output_root: product
    publication:
      - product/runtime.md
      - product/plans.md
      - product/customers.md
      - product/billing.md
      - product/usage.md
required_environment_variables: []
required_credential_files: []
---

# Takyon App Runtime

## Overview

Use this skill when the business app needs real customer auth, sessions, plans, entitlements, checkout, subscription reconciliation, revenue, or usage tracking.

## When to Use

- Use when a product needs real app customer state, not mock UI state.
- Use when pricing, checkout, or entitlement behavior must be wired honestly.
- Use when usage tracking or app budget gates matter.
- Do not use for pure page/layout work unless the runtime contract itself is changing.

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/runtime.md`, `product/plans.md`, `product/customers.md`, `product/billing.md`, `product/usage.md`
- Best call points: auth, billing, entitlements, checkout, usage wiring
- Publication location: `product/runtime.md`, `product/plans.md`, `product/customers.md`, `product/billing.md`, `product/usage.md`
- Tool names used by this skill: `business_read_business`, `business_read_file`, `business_upsert_app_surface_contract`, `business_configure_app_budget`, `business_upsert_app_plan`, `business_upsert_app_customer`, `business_grant_app_entitlement`, `business_request_app_magic_link`, `business_verify_app_magic_link`, `business_read_app_account`, `business_create_app_checkout`, `business_record_stripe_webhook`, `business_record_app_usage`, `business_write_file`, `business_patch_file`

## Prerequisites

- The Takyon toolset must be available.
- Start with `business_read_business`, then inspect any existing runtime mirrors with `business_read_file`.
- Keep top-level Takyon users and product customers separate; this skill only manages the product-customer side.
- If live auth, checkout, email, or billing depends on provider credentials, let the runtime tools expose the blocker instead of pretending those providers are already configured.
- If `product/surface.md` has no real `source_path` or `product/site/` does not exist yet, route software-business bootstrap work back through `takyon-build-product` before expanding runtime mirror docs.

## References

- `references/runtime-rules.md`

## Templates

- `templates/runtime.md`

## How to Run

- Call `business_read_business` first, then load `product/runtime.md`, `product/plans.md`, `product/customers.md`, `product/billing.md`, and `product/usage.md` with `business_read_file` if they already exist.
- Use `business_upsert_app_surface_contract` if the app surface contract is missing routes, source path, or truthful runtime notes.
- Use `business_configure_app_budget` and `business_upsert_app_plan` for plan policy, usage caps, and pricing metadata.
- Use `business_upsert_app_customer`, `business_grant_app_entitlement`, `business_request_app_magic_link`, `business_verify_app_magic_link`, and `business_read_app_account` for customer, session, and entitlement flows.
- Use `business_create_app_checkout` and `business_record_stripe_webhook` for paid checkout and reconciliation.
- Use `business_record_app_usage` for real usage metering.
- After tool-backed changes, mirror the truth into the canonical `product/` files with `business_write_file` or `business_patch_file`.

## Procedure

1. Call `business_read_business` and identify which runtime lane is actually changing: plan policy, auth/session, customer state, entitlement, checkout, billing, or usage.
2. If the product surface currently claims runtime features that are not wired, update the surface contract first with `business_upsert_app_surface_contract` or route the product copy repair back through `takyon-build-product`.
3. During bootstrap for a software business, do not let these runtime mirror files become the first main product artifact. If `product/site/` is still missing or `product/surface.md` has no real source path, route back to `takyon-build-product` first unless runtime-first work was explicitly requested.
4. For plan and budget work, call `business_configure_app_budget` and `business_upsert_app_plan` before editing any mirror files. Expect those tools to define the real limits and pricing state.
5. For customer and auth work, use `business_upsert_app_customer`, `business_grant_app_entitlement`, `business_request_app_magic_link`, `business_verify_app_magic_link`, and `business_read_app_account` in the order needed by the flow. If a provider or credential is missing, keep the blocker visible instead of faking a session.
6. For paid flows, call `business_create_app_checkout` to create the checkout intent and `business_record_stripe_webhook` when real Stripe events arrive. Do not claim paid entitlement or revenue until webhook reconciliation has happened.
7. For usage metering, call `business_record_app_usage` and reflect the resulting truth in `product/usage.md` and `product/billing.md`.
8. After real tool-backed changes, write or patch `product/runtime.md`, `product/plans.md`, `product/customers.md`, `product/billing.md`, and `product/usage.md` so they mirror actual runtime state and blockers.
9. If a feature is blocked by credentials, providers, or missing runtime setup, leave that path explicitly blocked in the publication files instead of inventing success.

## Output Format

- `product/runtime.md` should summarize the real shared runtime contract.
- `product/plans.md`, `product/customers.md`, `product/billing.md`, and `product/usage.md` should mirror canonical runtime state.

## Publication

- Publish the runtime overview to `product/runtime.md`.
- Publish mirrored plan, customer, billing, and usage state to the other canonical `product/` files.
- Any live provider-backed behavior must come from real app-runtime rails and receipts, not from these documents alone.

## Common Pitfalls

- Letting UI claims run ahead of actual runtime wiring
- Mixing top-level Takyon users with product customers
- Recording aspirational provider state as if it already exists

## Verification Checklist

- [ ] `product/runtime.md` matches the actual configured runtime behavior
- [ ] `product/plans.md`, `product/customers.md`, `product/billing.md`, and `product/usage.md` agree with the underlying tool-backed state
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
