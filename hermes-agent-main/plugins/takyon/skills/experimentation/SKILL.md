---
name: takyon-experimentation
description: Record and review measurable product, pricing, conversion, and distribution bets.
---

# Takyon Experimentation

Use this skill when the CEO chooses, runs, or reviews a measurable business bet: product changes, pricing tests, conversion fixes, campaign tests, onboarding changes, or channel experiments.

## Practice

- Do not treat experiments as a mandatory stage. Use this skill only when a testable bet is the chosen next move or an existing bet needs review.
- Start from current pulse, brain files, campaign/product/pricing workspaces, conversations, usage, revenue, and traffic evidence.
- Separate hypothesis from evidence. Record baseline, primary metric, guardrails, start date, review date, expected decision, and the smallest useful action.
- If canonical experiment tools exist in `business_registry`, use them. Until then, keep experiment records in business files such as `experiments/<name>/` or `brain/experiments.md`, and record important starts, observations, and decisions with `business_record_event`.
- If an experiment needs analytics, posting, email, spend, deploys, or provider calls, use guarded tools with `requires_api` or `requires_env`; do not claim live execution or results without receipts.
- Promote lessons that change strategy, ICP, pricing, product, or distribution into the business brain.

## Handoffs

Related skills may call this when they create a measurable bet:

- `takyon:conversion-review` for funnel and onboarding tests.
- `takyon:distribution-campaign` for channel, content, ad, and launch tests.
- `takyon:pricing-strategy` for offer, packaging, and price tests.
- `takyon:build-product` for product-surface or feature tests.

The CEO decides whether a bet is worth running. This skill keeps the bet honest.
