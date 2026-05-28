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

Use this skill when the business app needs real customer auth, sessions, plans, entitlements, checkout, subscription reconciliation, revenue, or usage tracking.

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/runtime.md`, `product/plans.md`, `product/customers.md`, `product/billing.md`, `product/usage.md`
- Best call points: auth, billing, entitlements, checkout, usage wiring
- Publication location: `product/runtime.md`, `product/plans.md`, `product/customers.md`, `product/billing.md`, `product/usage.md`

## References

- `references/runtime-rules.md`

## Templates

- `templates/runtime.md`

## When to Use

- Use when a product needs real app customer state, not mock UI state.
- Use when pricing, checkout, or entitlement behavior must be wired honestly.
- Use when usage tracking or app budget gates matter.

## Procedure

1. Read the existing product surface and current runtime state.
2. Use canonical business app tools for auth, customers, entitlements, checkout, billing, and usage.
3. Rewrite the publication paths so they reflect actual state.
4. Leave blocked features visibly blocked rather than faked.

## Output Format

- `product/runtime.md` should summarize the real shared runtime contract.
- `product/plans.md`, `product/customers.md`, `product/billing.md`, and `product/usage.md` should mirror canonical runtime state.

## Publication

- Publish the runtime overview to `product/runtime.md`.
- Publish mirrored plan, customer, billing, and usage state to the other canonical `product/` files.
- Any live provider-backed behavior must come from real app-runtime rails and receipts, not from these documents alone.

## Pitfalls

- Letting UI claims run ahead of actual runtime wiring
- Mixing top-level Takyon users with product customers
- Recording aspirational provider state as if it already exists

## Verification

- `product/runtime.md` matches the actual configured runtime behavior
- Plans, customers, billing, and usage mirrors agree with the underlying tool state
- Any blocked provider path is named explicitly instead of being hidden

## Rules

1. Do not emulate auth, sessions, checkout, billing, or subscriptions in browser-only state.
2. Treat top-level Takyon users and product customers as different scopes.
3. Record only actual runtime state or explicit blockers.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Missing provider credentials | Record the exact gate and leave the surface blocked |
| Product UI claims runtime features that are not real | Repair the UI or mark the feature blocked |
