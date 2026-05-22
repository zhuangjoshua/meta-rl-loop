# Hermes Usage

## Did V2 Use Hermes?

Yes.

V2 included:
- `vendor/argon-hermes-runtime`
- `.argon-hermes-home`
- setup/start scripts
- `src/lib/vendors/argon-runtime.ts`
- runtime reconciliation
- runtime session tables
- skill workflow invocation

## What Hermes Was Used For

Hermes was used for non-deterministic skill workflows:

- CEO wakeup/planning
- business planning
- market research
- social/content/support/outreach copy
- lead finding
- activity review
- other skill-style agent runs

Hermes submitted `/v1/runs`, recorded `runtime_sessions`, and later reconciled run status.

## What Hermes Was Not Used For

Hermes was not the source of truth.

It was not responsible for:
- Stripe checkout/webhooks
- X publish API call
- Meta API call
- cron state machine
- generated-app AI gateway
- project wallet reservation
- generated-app auth/payments
- generated-app build/deploy control

## V0 Decision

Keep Hermes where v2 used Hermes. The vendored runtime integration code, scripts, skill folder, and Argon adapter have now been copied into v3.

Hermes should run locally for v0. No VPS is required for the v0 implementation.

Current verified state:
- Copied: `vendor/argon-hermes-runtime`, setup/start/sync scripts, `skills/argon-company-factory`, `src/lib/vendors/argon-runtime.ts`.
- Not verified: local Hermes gateway process and a real `/v1/runs` receipt.
- Latest foundation E2E recorded Hermes as skipped because `ARGON_RUNTIME_URL` was not explicitly configured/running, then completed through the verified local-foundation provider path.

## Important Runtime Boundary

Even though the Hermes code is vendored in the repo, v2 still used it as a running local runtime service.

The app adapter posts to:

```text
ARGON_RUNTIME_URL /v1/runs
```

In v2 that defaults to:

```text
http://127.0.0.1:8642/v1/runs
```

So "copying Hermes code" means copying:
- `vendor/argon-hermes-runtime`
- runtime setup/start scripts
- skill sync/install behavior
- `src/lib/vendors/argon-runtime.ts`
- runtime session/reconciler logic
- workflow envelope logic

But Hermes-backed runs still require the local Hermes gateway process to be running. This is a local API boundary, not an external SaaS API and not a VPS requirement.

## V3 Implementation Rule

Do not remove Hermes.

Implement it as a scoped local runtime adapter for the same categories v2 used:
- CEO/planning
- market research
- social/content/support/outreach copy
- lead finding
- activity review

Keep deterministic side effects in app/local-worker code.
