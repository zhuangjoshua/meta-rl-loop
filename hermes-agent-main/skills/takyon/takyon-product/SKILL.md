---
name: takyon-product
description: >-
  Build or improve one business web product, including its public landing surface and real signed-in
  customer workflow, then verify the build and publication result. Use when the product is missing,
  starter-shaped, incomplete, or needs a focused iteration. Do not use for market research, channel
  distribution, or backend-rail invention.
---

# Product

Apply this method directly in the scoped product source using real business context, current research,
and one concrete customer goal. Runtime policy owns exact paths, tools, validators, authority, and
completion gates.

Do not use this for market research, ICP, or offer resets that still need fresh evidence; use
`takyon-market-research`. App-runtime backend rails such as auth, sessions, entitlements, checkout,
webhooks, and usage budgets belong to `takyon-app-runtime`.

## Choose the Phase

- First build: the public landing surface is missing or weak, or the signed-in shell is not truthful.
- Flesh out the app: the landing surface and account shell exist, but the signed-in product has no
  real workflow.
- Surgical iterate: one real flow, screen, action, conversion step, account issue, or price needs a
  focused pass.

## Method

1. Read business truth, current surface state, existing source, and relevant research; infer the
   phase from what actually exists.
2. For a first build, configure the surface and authoritative paid plan before making corresponding
   claims in the interface.
3. State one customer goal and implement one bounded pass. Preserve useful existing work and the
   established visual direction.
4. Use `design-taste-frontend` for marketing-surface craft and respect its boundary around dense
   product UI. Request generated imagery only for a defined page role.
5. Keep shared auth, account, entitlement, checkout, usage, and action behavior on the app-runtime
   contract; do not recreate those rails in client code.
6. Run the deterministic build, type, action, and path validators supplied by policy.
7. Request the declared publication capability and verify the structured result.

## Pricing Rule

Treat a live price offer as immutable for existing subscribers. A price change creates a new plan
version for new signups and retains the old offer for grandfathered customers. Update authoritative
plan state before changing pricing copy. Never move existing subscribers or enlarge included usage
by editing presentation code.

## Verification

- Build and type checks are green.
- Publication reports success with a real public location.
- Every changed runtime-backed action passes readiness, real invocation, and bound completion checks.
- Account and subscription state comes from the shared runtime contract.
- No first-build shell, placeholder, or local-only preview is presented as a finished product.

Do not report completion until every applicable binary floor above passes; an explicit blocker ends
the attempt without a workaround or simulated success.
