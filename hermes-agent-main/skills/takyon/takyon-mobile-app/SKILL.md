---
name: takyon-mobile-app
description: Build, compliance-check, and ship an iOS App Store app for a mobile_app-archetype Takyon business (Expo/EAS, TestFlight, App Store submit).
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, mobile, app-store, ios, expo, eas, product]
    related_skills: [takyon-product, takyon-app-runtime, takyon-market-research]
    requires_toolsets: [takyon, takyon-authority]
    requires_tools:
      - business_read_business
      - business_read_store_status
      - business_publish_mobile_release
      - business_claude_agent_task
      - business_read_file
      - business_list_files
    routing:
      owns: The whole iOS App Store surface for a mobile_app business — the Expo app source build, the greenlight pre-submission compliance gate, and the credit-gated EAS build → TestFlight → App Store release.
      when_to_use:
        - the business is archetype `mobile_app` and its iOS app source is missing, starter-shaped, or needs a focused product pass
        - a mobile_app business is ready to cut a build to TestFlight (lane `preview`) or submit to the App Store (lane `production`)
        - the CEO needs to know whether Apple will accept a submission (developer-account health / agreement state) before spending on a build
      do_not_use_for:
        - a web_saas or shopify business product surface; that is `takyon-product`
        - app-runtime backend rails (auth, sessions, entitlements, checkout, webhooks, usage budgets); those are `takyon-app-runtime` and are SHARED across archetypes
        - selecting or changing a business's archetype; that is set at create time from the archetype registry
  takyon:
    scope: business
    allowed_roots: [product, metrics]
    output_root: product
    publication:
      - product/surface.md
      - product/app
required_environment_variables: []
required_credential_files: []
---

# Takyon Mobile App

## Overview

One skill for the whole iOS App Store surface of a `mobile_app`-archetype business. Like `takyon-product`, it does not prescribe how to build a good app — that craft is the Claude Agent SDK worker's job. This skill owns the routing, the compliance gate, and the release choreography that turns an Expo app source into a shipped App Store build.

The mobile app rides the SAME subuser backend rails as a web product (auth, sessions, entitlements, checkout, usage budgets are archetype-agnostic and owned by `takyon-app-runtime`). What is mobile-specific is the client (an Expo/React Native app seeded from the `mobile_app_kit` scaffold), the pre-submission compliance gate (greenlight), and the publish path (EAS build → TestFlight → App Store submit) — a credit-gated action, not the free R2 web publish.

Infer the PHASE from the current app state:

- **First build** — no real iOS app source yet, or it is still the seeded scaffold. Delegate a build that turns the `mobile_app_kit` scaffold into the real app against the business's research and offer.
- **Surgical iterate** — the app is real and one screen, flow, or action needs a tight pass. Delegate a focused change, not a rebuild.
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
  - `business_claude_agent_task` — the coding worker that builds/iterates the Expo app source
  - `business_publish_mobile_release` — the credit-gated EAS build → TestFlight/App Store release
  - `business_read_file` / `business_list_files` — inspect current app source before delegating

## Prerequisites

- The business must be archetype `mobile_app`. The mobile archetype is **gated until its end-to-end pipeline is proven** (`readmodular.md` §5): `business_publish_mobile_release` fails closed with `archetype_unavailable:mobile_app` until it is enabled in the archetype registry. Treat that error as "not yet shipped," not as a bug to patch.
- The App Store rails need two provider identities that live ONLY in the safebox (never on the runtime plane, never in `os.environ`): the Expo access token (`expo`) and the App Store Connect API key (`app_store_connect`). Both are resolved server-side through the safebox authority route; this skill never reads them.
- A real build additionally needs the one-time interactive `eas credentials` Apple-ID login to be completed by the operator. Until then `business_publish_mobile_release` returns `eas_builder_unconfigured` and **reserves no credits** — a truthful blocked receipt, not a fake success. Record the blocker and stop; do not fabricate a build id.
- The publish action spends **creative credits** (a fixed operator-priced action). It fails closed on insufficient credits, reserves before the provider call, settles at the build TRIGGER (the irreversible-spend point), and releases only if the trigger never happened.

## How to Run

- Read the business first: `business_read_business` to confirm `archetype == mobile_app`, then `business_list_files` / `business_read_file` on `product/app` to see the current app source and `product/surface.md` for the design brief.
- **Build / iterate the app source** through `business_claude_agent_task` pointed at the `product/app` workspace, exactly as `takyon-product` delegates web builds. The platform/app contract is injected into the worker by code — point the worker at the goal and the research, do not restate platform invariants in the instruction.
- **Before a release, read Apple account health** with `business_read_store_status`. If `account_health.state` is `agreement_blocked` (or any non-`ok`), stop and surface it: store submissions are frozen account-wide and a build would waste credits. Only proceed to a build when the account is healthy (or when cutting a `preview`/TestFlight build that does not require an accepted paid-apps agreement).
- **Cut the release** with `business_publish_mobile_release` (`lane: preview` for TestFlight-internal, `lane: production` for App Store submit). This runs the greenlight pre-submission compliance gate, reserves creative credits, invokes EAS, and settles at the successful trigger. A long build runs on the worker plane (`store.build` job) so it does not block the turn.
- **Verify before claiming success**: re-read the tool result's `build_id` (a real trigger) or its blocker. If the result is `eas_builder_unconfigured` or `archetype_unavailable`, report `blocked` with the exact reason — never `published`.

## Procedure

1. `business_read_business` → confirm `archetype == mobile_app`. If it is `web_saas`/`shopify_commerce`, hand off to `takyon-product`.
2. Inspect current app source: `business_list_files` on `product/app`, `business_read_file` on `product/surface.md`.
3. If the app source is missing/starter or needs a change, delegate the smallest honest build/iterate via `business_claude_agent_task` (workspace `product/app`), given the research and offer.
4. When a release is requested, first call `business_read_store_status`. Stop on a non-`ok` account state for a `production` submit.
5. Call `business_publish_mobile_release` with the chosen `lane` and an `idempotency_key`. Let it run the compliance gate + credit-gated EAS build.
6. Re-read the tool result. Report `published`/`queued` only on a real `build_id`; report `blocked` (with the exact gate token) on `archetype_unavailable`, `eas_builder_unconfigured`, insufficient credits, or a compliance failure.
7. Do not add skill-local money, compliance, or publish logic. The credit reserve→settle-at-trigger→release, the greenlight gate, and the store_release adapter live in the shared tools/rails; this skill only orchestrates them.

## Output Format

- `product/app` — the real Expo/React Native app source (built by the worker, not hand-authored here).
- `product/surface.md` — the design brief/source of truth the worker and this skill inspect.
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
- Restating the platform/app worker contract in the delegation instruction. It is injected by code; duplicating it drifts from the source of truth.

## Verification Checklist

- [ ] Business archetype is `mobile_app` (confirmed via `business_read_business`)
- [ ] App source changes were made by `business_claude_agent_task`, not hand-authored in this skill
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
| `archetype_unavailable:mobile_app` | The mobile pipeline is not enabled yet (`readmodular.md` §5). Record the blocker; do not patch around it. |
| `eas_builder_unconfigured` | The jailed EAS builder and/or the one-time `eas credentials` Apple-ID login is not provisioned. Record the blocker; no credits were reserved. |
| `account_health.state != ok` | Apple developer-account issue (often `agreement_blocked`). Resolve in App Store Connect before a `production` submit; a `preview`/TestFlight build may still be possible. |
| Insufficient creative credits | The release fails closed before the provider call. Surface the shortfall; do not proceed. |
| Compliance gate failure (greenlight) | The pre-submission scan found a blocker. Fix the app source via `business_claude_agent_task` and re-run the release. |
