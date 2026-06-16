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
    requires_tools: [business_read_business, business_upsert_app_plan, business_request_app_magic_link, business_create_app_checkout, business_check_runtime_capabilities, business_invoke_app_action, business_send_app_email, business_list_app_media]
    routing:
      owns: auth, sessions, checkout, entitlements, billing, usage, backend product actions, and runtime wiring for the business app
      when_to_use:
        - real app customer state, pricing, checkout, or entitlements must be wired honestly
        - usage tracking or app budget gates materially affect the product
        - verifying or operating declared product actions before reporting a product workflow as working
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
- Tool names used by this skill: `business_read_business`, `business_read_file`, `business_upsert_app_surface_contract`, `business_check_runtime_capabilities`, `business_configure_app_budget`, `business_upsert_app_plan`, `business_upsert_app_customer`, `business_upsert_app_profile`, `business_grant_app_entitlement`, `business_request_app_magic_link`, `business_verify_app_magic_link`, `business_read_app_account`, `business_read_app_profile`, `business_create_app_checkout`, `business_record_stripe_webhook`, `business_record_app_usage`, `business_invoke_app_action`, `business_send_app_email`, `business_write_file`, `business_patch_file`
- Product actions: implement real `product/site/actions/<name>.ts` files, call them from the UI through `createActionRunner(name)` / `invokeAction(name)`, and verify through runtime receipts under `metrics/receipts/app-actions/`.
- Actions runtime config: `plugins.takyon.app_actions.*`, especially `rails_base_url` for scheduled actions plus the Deno capability gate
- Frontend conventions: product UIs drive `http` actions through the shared runtime client's `createActionRunner(name)` (pending guard, typed error kinds, budget errors carry an upgrade `checkoutUrl`); schedule-triggered actions persist output through the records rail and the UI reads it back with `listRecords` — no polling, no fabricated activity feeds
- Scaffold target: products consume the kit through the scaffold's `src/lib/takyon.ts` + `src/lib/hooks.ts` (`useSession`, `useRecords`, `useActionRunner`) — those hooks are editable product source wrapping this kit's client, which stays canonical; never fork the kit itself into product code
- Product media (images/avatars): declare the `media` rail, then upload from the UI through the kit's `uploadMedia(file)` (multipart) and reference stored images via `mediaUrl(id)` — never base64-into-records. Allowed types image/jpeg,png,webp,gif under per-user (50MB) and per-business (1GB) byte quotas; serving is session-gated; each store meters as a `media_store` usage event with a receipt under `metrics/receipts/app-media/`. `business_list_app_media` reports usage/quota. Records-v2: for feeds/browse, pass `listRecords({filters, sort, cursor})` (bounded server-side query) instead of fetching all records.
- Product email: declare the `email` rail, then send with `business_send_app_email` (recipient `app_user_id`, `subject`, `text`, slug-like `purpose`, `idempotency_key`). Test mode suppresses the real send and writes a receipt under `metrics/receipts/app-email/`; live sends go through the platform Postmark credentials and meter as `email_send` usage events with a per-business daily cap. Schedule-triggered actions send via `POST email/send` with their service session; customer sessions and service-identity recipients are refused truthfully. Verification = send in test mode, then read back the receipt and the `email_send` usage event — a suppressed test send proves the rail wiring, not live deliverability

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
- Treat the shared app-runtime rails as the canonical backend for auth, account, checkout, profile, and actions on product apps.
- Use `business_upsert_app_surface_contract` if the app surface contract is missing routes, source path, or truthful runtime notes.
- Use `business_configure_app_budget` and `business_upsert_app_plan` for plan policy, usage caps, and pricing metadata.
- Use `business_upsert_app_customer`, `business_upsert_app_profile`, `business_grant_app_entitlement`, `business_request_app_magic_link`, `business_verify_app_magic_link`, `business_read_app_account`, and `business_read_app_profile` for customer, profile, session, and entitlement flows.
- Use `business_create_app_checkout` and `business_record_stripe_webhook` for paid checkout and reconciliation. On a paid event, reconciliation also accrues the gross minus the platform application fee into the business owner's custody balance (flow B); report that as owed/accrued, not paid out.
- Use `business_record_app_usage` for real usage metering.
- Use `business_check_runtime_capabilities` before claiming actions work. If the actions/deno probe is unavailable, record the blocker and stop instead of simulating an invocation.
- Use `business_invoke_app_action` plus receipt read-back to verify real `http` actions. For scheduled actions, verify through a real tick and a service-principal receipt rather than impersonation or owner-plane shortcuts.
- For product AI generation, the shared broker route still exists (`POST /generate` on product hosts, or `POST /api/takyon/apps/<slug>/generate` off-host), but customer-facing products should reach it through action files rather than direct client calls. Old stored `frontend_stack=legacy` values normalize onto the pinned scaffold for compatibility; do not preserve direct-client `generate` as a supported product shape.
- The prepared `_takyon/` kit inside `product/site/` is a shared substrate, not a claim that every rail is live. Keep `rail_state` truthful so the kit and delegated worker see the same runtime reality.
- After tool-backed changes, update `product/surface.md` so the visible contract matches the real runtime state. Only write extra runtime markdown when the operator explicitly wants it.

## Procedure

1. Call `business_read_business` and identify which runtime area is actually changing: plan policy, auth/session, customer state, profile state, entitlement, checkout, billing, or usage.
2. Read `runtime_features` from the app surface contract and treat that list as the source of truth for which runtime-backed claims this product shell expects now.
3. If the product surface currently claims runtime features that are not wired, update the surface contract first with `business_upsert_app_surface_contract` or route the product copy repair back through `takyon-build-product`.
4. During bootstrap for a software business, do not let these runtime mirror files become the first main product artifact. If `product/site/` is still missing or `product/surface.md` has no real source path, route back to `takyon-build-product` first unless runtime-first work was explicitly requested.
5. For plan and budget work, call `business_configure_app_budget` and `business_upsert_app_plan` before editing any notes. Expect those tools to define the real limits and pricing state.
6. For customer/profile/auth work, use `business_upsert_app_customer`, `business_upsert_app_profile`, `business_grant_app_entitlement`, `business_request_app_magic_link`, `business_verify_app_magic_link`, `business_read_app_account`, and `business_read_app_profile` in the order needed by the flow. If a provider or credential is missing, keep the blocker visible instead of faking a session.
7. For paid flows, call `business_create_app_checkout` to create the checkout intent and `business_record_stripe_webhook` when real Stripe events arrive. Do not claim paid entitlement or revenue until webhook reconciliation has happened. On a paid `checkout.session.completed`, that reconciliation does three things in one atomic step: records the revenue event, grants the paying sub-user's entitlement, AND accrues the gross minus the platform application fee (`STRIPE_CONNECT_APPLICATION_FEE_BPS`, default 2000 bps = 20%) into the business owner's custody balance (flow B — the sub-user→owner custody rail, distinct from the top-level Takyon user's billing ledger). Payout of that custody balance to the owner is deferred (no Stripe Connect transfer is performed yet), so reflect it truthfully in `product/surface.md` or an explicitly requested billing note, never as money already paid out.
8. For usage metering, call `business_record_app_usage`. Product AI generation is metered through the canonical runtime route (`POST /generate` on product hosts, or `POST /api/takyon/apps/<slug>/generate` off-host): that route brokers server-side through the shared Takyon AI authority and reserves then settles against the budget set by `business_configure_app_budget`. On the pinned Vite scaffold, action code reaches that broker via `ctx`; the browser should call a named action, not `/generate` directly. Until a dedicated usage read rail is finished on the app plane, treat `business_read_app_account` as the current read source for usage summary in the product shell. Surface `402` (over budget) and `503` (generation not configured) honestly, and never embed the platform provider key in the app or call internal authority endpoints directly from product code.
9. For product actions, treat the code as the source of truth: confirm UI action call names match real files under `product/site/actions/`, and verify those actions with real receipts. For the exact gated workflow doctrine, read `takyon-product-workflow`; for the runtime recipe, this skill is authoritative.
10. Capability gate before action verification: call `business_check_runtime_capabilities` and check the Deno/actions probe. If Deno or the rail runtime is missing, record the exact blocker and stop; do not pretend an invocation happened.
11. HTTP action verification recipe: in test mode, create a normal test customer, request a magic link, verify it, capture the resulting `session_token`, then call `business_invoke_app_action {business, action, payload, session_token}`. The invoke tool never mints or impersonates sessions for you.
12. Receipt read-back is mandatory: after every invoke, open the receipt under `metrics/receipts/app-actions/` and verify the usage event exists with `purpose=action_invoke`. An invoke without receipt read-back does not count as verification.
13. Schedule action verification recipe: trigger a real tick, then confirm the schedule receipt shows `principal: "service"`, the schedule row advanced, and the service identity gained no customer profile or directory presence.
14. When an action fails, route the fix back to the action file or contract truthfully, then re-run the same verification recipe. `402` means real budget exhaustion, `429` means rate limiting or an already-running action, and Deno sandbox/timeout failures are real runtime failures, not copy issues.
15. After real tool-backed changes, write or patch `product/surface.md` so it reflects actual runtime state and blockers. That same contract refreshes the shared `_takyon/` kit inside `product/site/`.
16. If a feature is blocked by credentials, providers, Deno, missing rails, or missing runtime setup, leave that path explicitly blocked in `product/surface.md` instead of inventing success.

## Output Format

- `product/surface.md` should summarize the real shared runtime contract, selected rails, and visible blockers.

## Publication

- Publish the runtime overview and blockers to `product/surface.md`.
- Any live provider-backed behavior must come from real app-runtime rails and receipts, not from these documents alone.

## Common Pitfalls

- Letting UI claims run ahead of actual runtime wiring
- Mixing top-level Takyon users with product customers
- Recording aspirational provider state as if it already exists
- Publishing product actions without a real invoke plus receipt read-back
- Treating `included_action_quota` as an active action allowance; it is inert metadata, not a live runtime gate
- Expecting npm packages, remote imports, filesystem writes, or env access inside an action
- Using an owner-plane token on the app plane instead of a real app session token
- Embedding the platform provider key in the generated app, or on the pinned Vite scaffold bypassing real action files and calling the shared generate rail directly from product code

## Verification Checklist

- [ ] `product/surface.md` matches the actual configured runtime behavior
- [ ] Any blocked provider path is named explicitly instead of being hidden
- [ ] No product customer, entitlement, checkout, or billing claim appears without the corresponding runtime tool truth
- [ ] Every product action that the UI calls was invoked at least once in the current mode and the latest receipt was read back
- [ ] The latest refresh/surface evidence shows no UI-referenced action with `never` invocation status
- [ ] Every scheduled product action has a real service-principal receipt from a real tick
- [ ] Deno capability is confirmed or the exact blocker is recorded instead of a publish claim
- [ ] No service identity appeared in customer or directory state

## Rules

1. Treat top-level Takyon users and product customers as different scopes.
2. Record only actual runtime state or explicit blockers.
3. Never report an action as working without invoke plus receipt read-back.
4. Actions meter flat per invocation while action-internal AI calls still meter through the shared generate rail; do not double-represent or hide cost.
5. Spend authority stays in the rails and receipts, not in skill prose or client code.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Missing provider credentials | Record the exact gate and leave the surface blocked |
| Product UI claims runtime features that are not real | Repair the UI or mark the feature blocked |
| `business_invoke_app_action` returns `402` | The app budget is genuinely exhausted; reconfigure the budget or accept the blocker instead of bypassing it |
| `business_invoke_app_action` returns `429` | Treat it as truthful rate limiting or `action_already_running`; wait, retry once, or reduce overlapping triggers |
| Actions rail is blocked because Deno is missing | Re-check runtime capabilities, record the blocker, and do not claim action verification succeeded |
| A product action still shows `never` | The CEO never actually verified it; run the invoke/tick recipe and read back the receipt before claiming the workflow works |
| `action <name> has no file` or outbound-host validation fails | The worker never wrote the real action file or the source drifted; fix the file and re-run verification |
