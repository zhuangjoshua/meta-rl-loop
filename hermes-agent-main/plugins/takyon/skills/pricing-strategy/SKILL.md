---
name: takyon-pricing-strategy
description: Improve pricing, packaging, checkout, margin, and revenue strategy.
---

# Takyon Pricing Strategy

Use this skill when conversion exists without revenue, revenue has weak margin, the offer is unclear, or the business needs a stronger pricing hypothesis.

## Practice

- Read current pricing, conversion evidence, ledger, and relevant campaign learnings.
- Propose pricing or packaging changes with the evidence behind them.
- Write pricing memory under `brain/pricing.md` or a sharper file.
- Use `business_upsert_app_plan` for product app plan policy, included usage, billing interval, Stripe price linkage, and entitlement tier. Use `business_configure_app_budget` for the app's overall usage budget.
- Use `business_create_app_checkout` only when Stripe credentials, success/cancel URLs, and authority are explicit; in test mode it may create a local checkout receipt but not live payment state. Reconcile real payment/subscription state through `business_record_stripe_webhook`.
- Record site/pricing work requests with `business_enqueue_job` only when no canonical app tool applies.
- Record risks, test criteria, and rollback notes.

Do not allocate spend or change checkout unless the budget, APIs, authority, and receipt path are explicit.
