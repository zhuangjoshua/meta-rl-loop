---
name: takyon-app-runtime
description: >-
  Configure and verify a business application's declared customer-facing runtime features and real
  signed-in actions. Use when auth, profiles, plans, entitlements, checkout, usage, or actions must
  be wired and proven through existing rails. Do not use to administer real customers, forge
  payment/webhook state, or perform purely visual layout work.
---

# App Runtime

Use this method when a customer-facing application needs real state and behavior rather than a
convincing shell. Top-level operators and product customers are different identities; never merge
their sessions, budgets, permissions, or data.

Use it when usage tracking or app budget gates materially affect the product, or when real customer
state, pricing, checkout, entitlements, or declared actions must be wired honestly. It also owns
verifying or operating declared product actions before reporting a product workflow as working.

## Method

1. Read current business and surface truth. Identify the one runtime area changing: identity,
   profile, plan, entitlement, checkout, billing reconciliation, usage, media, email, or a declared
   action.
2. Confirm that the selected runtime feature is declared before exposing it in the interface.
3. Configure or exercise the feature through the semantic capabilities in `contract.yaml`.
4. Keep customer sessions customer-scoped and service identities service-scoped; never impersonate a
   customer to make verification pass.
5. For a paid flow, treat provider reconciliation as the source of truth. A checkout intent alone is
   not payment, entitlement, revenue, or payout.
6. For usage-bearing behavior, preserve the active paid plan's budget and refuse work when authority
   is unavailable or exhausted.
7. For a declared action, verify platform readiness, invoke the real action with an appropriate
   principal, and read the bound completion evidence.
8. Update the runtime contract with only observed state and explicit blockers.

Customer/profile/entitlement administration, provider webhook recording, and authoritative usage
ledger writes remain backend/operator authority surfaces; this method wires and verifies their
customer-facing behavior through the safe capabilities exposed for the current invocation.

## Runtime Rules

- No active paid entitlement means no implied paid access or bundled usage budget.
- Keep plan economics immutable for existing subscribers; pricing changes create a new offer and
  preserve grandfathered subscriptions.
- Product code calls named, declared actions rather than internal authority endpoints.
- Scheduled actions use service identity and must not create customer profiles or directory entries.
- Media uses the declared media rail; do not encode large files into general records.
- Test suppression proves wiring only, not live deliverability or provider success.
- Missing credentials, runtime support, or authority is a blocker, not permission to simulate state.

## Verification

- The visible runtime contract agrees with actual configured behavior.
- Every changed action has a real readiness check, invocation, and bound completion record.
- Paid access follows reconciled payment state, not optimistic client state.
- No operator identity, provider secret, or internal authority endpoint appears in customer code.
- Any unavailable feature is named as blocked rather than represented as working.

Read [references/runtime-rules.md](references/runtime-rules.md) for domain constraints that are
independent of a particular runtime binding.
