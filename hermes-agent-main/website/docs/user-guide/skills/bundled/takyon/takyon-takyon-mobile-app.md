---
title: "Takyon Mobile App — Build, verify, compliance-check, and release an iOS application for a mobile-app business"
sidebar_label: "Takyon Mobile App"
description: "Build, verify, compliance-check, and release an iOS application for a mobile-app business"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Takyon Mobile App

Build, verify, compliance-check, and release an iOS application for a mobile-app business. Use when real mobile source is missing, starter-shaped, or needs a focused pass, or when a verified build should move through TestFlight or App Store submission. Do not use for web products, shared backend runtime ownership, or archetype selection.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/takyon/takyon-mobile-app` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Takyon loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Takyon Mobile App

## Overview

One skill for the whole iOS App Store surface of a `mobile_app`-archetype business. The primary
Claude Agent SDK session applies the app craft directly. This skill owns routing, the compliance
gate, and the release choreography that turns Expo source into a shipped App Store build.

The mobile app rides the SAME subuser backend rails as a web product (auth, sessions, entitlements, checkout, usage budgets are archetype-agnostic and owned by `takyon-app-runtime`). What is mobile-specific is the client (an Expo/React Native app seeded from the `mobile_app_kit` scaffold), the pre-submission compliance gate (greenlight), and the publish path (EAS build → TestFlight → App Store submit) — a credit-gated action, not the free R2 web publish.

Infer the PHASE from the current app state:

- **First build** — no real iOS app source yet, or it is still the seeded scaffold. Turn the
  `mobile_app_kit` scaffold into the real app against the business's research and offer.
- **Surgical iterate** — the app is real and one screen, flow, or action needs a tight pass. Make a
  focused change, not a rebuild.
- **Release** — the app source is ready and it is time to cut a TestFlight build (`lane: preview`) or submit to the App Store (`lane: production`).

## When to Use

- Use when the business archetype is `mobile_app` and its iOS app needs to be built, iterated, or released.
- Use before a release to read Apple developer-account health, so a frozen agreement does not waste a paid build.
- Do not use for web/Shopify product surfaces, shared backend rails, or archetype selection (see `do_not_use_for`).

## Quick Reference

- Primary root: `product/` (the iOS app source lives under `product/app`; the marketing/support/privacy web surface stays under `product/site`)
- Publication paths: `product/surface.md`, `product/app`
- Tool names used by this skill:
  - `business_read_business` — read the business row; confirm `archetype == mobile_app`
  - `business_read_store_status` — Apple developer-account health (agreement/auth state) before a release
  - `business_read_file` / `business_list_files` / `business_write_file` /
    `business_patch_file` — inspect and edit Expo source directly in this session
  - `business_publish_mobile_release` — the credit-gated EAS build → TestFlight/App Store release
  - `business_read_file` / `business_list_files` — inspect current app source before delegating

## Prerequisites

- The business must be archetype `mobile_app` (`business_read_business` confirms; the archetype is enabled in the registry, selectable at create with `--archetype app`).
- The App Store rails need two provider identities that live ONLY in the safebox (never on the runtime plane, never in `os.environ`): the Expo access token (`expo`) and the App Store Connect API key (`app_store_connect`). Both are resolved server-side through the safebox authority route; this skill never reads them.
- The build lane is **host-independent**: on a host with local builder custody the build signs locally; on any other host `business_publish_mobile_release` mints the per-build signing bundle through the safebox `build-credentials` route (the ASC key never leaves the safebox). If NEITHER lane resolves, the tool returns `eas_builder_unconfigured` and **reserves no credits** — a truthful blocked receipt. Record it and stop; do not fabricate a build id.
- The publish action spends **creative credits** (a fixed operator-priced action). It reserves before the provider call, settles at the build TRIGGER (the irreversible-spend point), and releases only if the trigger never happened. On operator-owned businesses the platform may run with the operator creative-gate bypass (never refused, still fully metered); customer/subuser surfaces stay hard-gated.

## How to Run

- Read the business first: `business_read_business` to confirm `archetype == mobile_app`, then `business_list_files` / `business_read_file` on `product/app` to see the current app source and `product/surface.md` for the design brief.
- **Build / iterate the app source** directly in this primary SDK session through the scoped business
  file tools under `product/app`. The platform/app contract is injected by runtime policy; apply it
  without restating or weakening it, and never spawn another agent.
- **Before a release, read Apple account health** with `business_read_store_status`. If `account_health.state` is `agreement_blocked` (or any non-`ok`), stop and surface it: store submissions are frozen account-wide and a build would waste credits. Only proceed to a build when the account is healthy (or when cutting a `preview`/TestFlight build that does not require an accepted paid-apps agreement).
- **Cut the release** with `business_publish_mobile_release` (`lane: preview` for TestFlight-internal, `lane: production` for App Store submit). This runs the greenlight pre-submission compliance gate, reserves creative credits, invokes EAS, and settles at the successful trigger. A long build runs on the worker plane (`store.build` job) so it does not block the turn.
- **Verify before claiming success**: re-read the tool result's `build_id` (a real trigger) or its blocker. If the result is `eas_builder_unconfigured` or `archetype_unavailable`, report `blocked` with the exact reason — never `published`.

## Procedure

1. `business_read_business` → confirm `archetype == mobile_app`. If it is `web_saas`/`shopify_commerce`, hand off to `takyon-product`.
2. Inspect current app source: `business_list_files` on `product/app`, `business_read_file` on `product/surface.md`.
3. If the app source is missing/starter or needs a change, implement the smallest honest
   build/iterate directly under `product/app`, grounded in the research and offer.
4. When a release is requested, first call `business_read_store_status`. Stop on a non-`ok` account state for a `production` submit.
5. Call `business_publish_mobile_release` with the chosen `lane` and an `idempotency_key`. Let it run the compliance gate + credit-gated EAS build.
6. Re-read the tool result. Report `published`/`queued` only on a real `build_id`; report `blocked` (with the exact gate token) on `archetype_unavailable`, `eas_builder_unconfigured`, insufficient credits, or a compliance failure.
7. If the triggered build later FAILS on the EAS builder, run the **Build-Failure Triage** loop below — never leave a failed build as the final state without either a fixed re-trigger or a recorded blocker.
8. Do not add skill-local money, compliance, or publish logic. The credit reserve→settle-at-trigger→release, the greenlight gate, and the store_release adapter live in the shared tools/rails; this skill only orchestrates them.

## Build-Failure Triage (the repair loop)

A triggered build can still fail on the remote EAS builder (the trigger settled the credits — that spend is real; the fix loop makes it not wasted). Bounded: **maximum 3 total build attempts per release intent**, then stop and report the receipt trail.

1. **Detect**: `business_read_store_status {business, build_id}` — the `build` block returns `status` (`finished` | `errored` | in-progress states), the signed `artifact_url` when finished, and the provider `error` message when errored. The publish receipt also carries `logs_url` (the expo.dev build page with full phase logs).
2. **Classify** the failure against the known defect table:
   - `ERESOLVE` / dependency conflict during install → a package.json/package-lock drift; pin the conflicting dep (the scaffold pins `react-dom` to match `react` for exactly this reason).
   - `MAC verification failed` / "Distribution certificate ... hasn't been imported" → p12 packaging defect (platform-side; not fixable from the app source — record as a platform blocker).
   - "doesn't support the X capability" during Xcode signing → app.json declares an entitlement the profile lacks; either drop the entitlement from app.json or re-publish (the credential mint re-syncs capabilities and re-mints the profile).
   - Config-plugin crash / `expo config` failure → a plugin or app.config edit that breaks evaluation; remove/fix the plugin (managed workflow only).
   - Metro/bundle errors → real source defects; fix the named files.
3. **Patch**: make the smallest fix directly under `product/app`, then let the scoped release/build
   rail re-verify `npm ci` + `tsc` + `expo config` so the same defect class cannot return
   unverified.
4. **Re-trigger**: `business_publish_mobile_release` with a **FRESH `idempotency_key`** — a replayed key returns the PRIOR receipt by design and will never trigger a new build.
5. **Converge**: after a defect class is fixed, note it in `metrics/` — recurring classes belong baked into the `mobile_app_kit` scaffold or the builder (report that recommendation; do not edit platform code from this skill).
6. **Stop condition**: 3 attempts exhausted, a platform-side blocker (p12/custody/account), or a credit refusal — report `blocked` with the exact receipts and stop.

## Output Format

- `product/app` — the real Expo/React Native app source edited by the primary SDK session.
- `product/surface.md` — the design brief/source of truth this skill inspects.
- `product/site` — the marketing/support/privacy web surface (App Store requires those URLs); it publishes to R2 through the normal web path, unchanged.
- Release truth is the `business_publish_mobile_release` tool result / receipt (`build_id`, lane, credits settled), never a hand-written claim.

## Publication

- Durable app source: `product/app`.
- Design source of truth: `product/surface.md`.
- Live release proof: the `business_publish_mobile_release` tool result (`build_id` + settled creative-credit receipt) and, for account standing, the `business_read_store_status` result. If a release is blocked, the honest publication is the recorded blocker, not a fake build id.

## Common Pitfalls

- Claiming a build shipped when the tool returned `eas_builder_unconfigured` or `archetype_unavailable`. Those are truthful fail-closed states; report them as blocked.
- Reading a provider key from `os.environ` or trying to run EAS on the runtime plane. The Expo/ASC keys live only in the safebox and are resolved server-side; the runtime plane never sees them.
- Triggering a paid EAS build on every marketing-copy tweak. The app binary release is a separate credit-gated action; the web marketing surface publishes freely through `product/site`.
- Submitting to `production` while the Apple developer account is `agreement_blocked`. Read `business_read_store_status` first.
- Restating the injected platform/app contract. It is supplied by code; duplicating it drifts from
  the source of truth.

## Verification Checklist

- [ ] Business archetype is `mobile_app` (confirmed via `business_read_business`)
- [ ] App source changes were made directly through the scoped business file tools in this session
- [ ] Apple account health was read before a `production` submit
- [ ] Any claimed release is backed by a real `build_id` in the `business_publish_mobile_release` result; blocked states are reported with their exact gate token
- [ ] No skill-local money/compliance/publish logic was added; the shared rails were used
- [ ] The marketing/support/privacy web surface stays under `product/site` and publishes through the normal web path

## Rules

1. Keep work business-scoped to the one `mobile_app` business.
2. Do not fake build state, TestFlight/App Store submission, provider state, or credit spend.
3. Use `business_publish_mobile_release` for the app binary release and the shared web path for the marketing surface — never couple them.
4. If a needed state change has no `business_*` tool yet, add the tool and tests in the same change (see `BUILDING-SKILLS-AND-TOOLS.md`).

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `archetype_unavailable:<key>` | That archetype is registered but not enabled in the registry (mobile_app IS enabled; shopify_commerce is not). Record the blocker; do not patch around it. |
| `eas_builder_unconfigured` | Neither builder lane resolves on this plane: no local custody AND the safebox build-credentials route unreachable/unconfigured (or node/npm/npx/git missing). Record the blocker; no credits were reserved. |
| Build triggered but later `errored` | Run the Build-Failure Triage loop above (max 3 attempts, fresh idempotency_key each). |
| `account_health.state != ok` | Apple developer-account issue (often `agreement_blocked`). Resolve in App Store Connect before a `production` submit; a `preview`/TestFlight build may still be possible. |
| Insufficient creative credits | The release fails closed before the provider call. Surface the shortfall; do not proceed. |
| Compliance gate failure (greenlight) | The pre-submission scan found a blocker. Fix the app source directly in this session and re-run the release. |
