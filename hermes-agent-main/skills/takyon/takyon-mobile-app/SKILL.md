---
name: takyon-mobile-app
description: >-
  Build, verify, compliance-check, and release an iOS application for a mobile-app business. Use
  when real mobile source is missing, starter-shaped, or needs a focused pass, or when a verified
  build should move through TestFlight or App Store submission. Do not use for web products,
  shared backend runtime ownership, or archetype selection.
---

# Mobile App

Own the iOS client and release choreography for a business already classified as a mobile
application. Shared auth, sessions, entitlements, checkout, and usage remain owned by the app-runtime
method.

This is the whole iOS App Store surface method: Expo app source build, greenlight pre-submission
compliance, EAS build, TestFlight preview, and App Store release through bound authority.

## Choose the Phase

- First build: source is missing or still starter-shaped.
- Surgical iterate: one real screen, flow, or action needs a focused change.
- Release: verified source should move to an internal preview or store submission.

Use the release phase when the operator needs to know whether Apple will accept a submission from
current developer-account health and agreement state before committing a build.

Do not use this for a web-SaaS or commerce product surface; use `takyon-product`. Do not use it for
shared app-runtime backend rails or for selecting or changing the business archetype, which is set at
creation time from the archetype registry.

## Method

1. Confirm the mobile archetype and inspect current source, business evidence, and design direction.
2. For build or iteration, make one bounded product pass in the scoped app source. Preserve the
   approved scaffold and shared backend contracts.
3. Run deterministic install, type, configuration, and compliance checks supplied by policy.
4. Before a store submission, read developer-account health and stop when agreements or custody
   state would prevent acceptance.
5. Request the bound release capability with a stable intent identifier and the appropriate preview
   or production lane.
6. Report queued or released only when a real build identifier is returned; otherwise report the
   exact blocker.
7. If a triggered build fails, classify the provider error, make the smallest source fix when it is
   source-owned, and retry with a fresh intent identifier. Stop after three total attempts or on a
   platform-owned blocker.

## Failure Classification

- Dependency resolution or bundle errors: repair the named source or dependency drift.
- Configuration evaluation failures: repair the invalid configuration or plugin.
- Signing, certificate, custody, or account-agreement failures: report a platform blocker.
- Missing builder, authority, or release capability: stop without fabricating a build.

## Verification

- The business is mobile-app archetyped and changes are confined to its scoped app source.
- Deterministic source and compliance checks pass before release.
- Production submission follows a current account-health check.
- Every claimed build has a real provider build identifier; failures preserve their exact class.
- Shared backend behavior was not reimplemented in the client.
