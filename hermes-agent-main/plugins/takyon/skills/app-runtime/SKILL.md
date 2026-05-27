---
name: takyon-app-runtime
description: Configure business product app auth, customers, entitlements, checkout, subscriptions, usage budgets, and runtime surface contracts.
---

# Takyon App Runtime

Use this skill when a business product needs real customer signup, magic-link login, product subusers, plan policy, entitlements, Stripe checkout, subscription reconciliation, revenue tracking, app usage budget controls, or a frontend/runtime contract.

## Practice

- Read the business state first, including `app/index.md` if it exists.
- Keep the distinction clear: Takyon users are top-level operators; app users/product subusers are customers of a business product.
- The runtime rails do not decide what the app looks like. Record visual/source ownership with `business_upsert_app_surface_contract`, usually pointing to `product/design-brief.md` and the business-owned frontend source path.
- Use `business_configure_app_budget` to set the app's overall usage budget cap before recording paid or AI-backed product usage.
- Use `business_upsert_app_plan` for free/paid/owner plan policy, included usage, billing interval, and Stripe price linkage.
- Use `business_request_app_magic_link` and `business_verify_app_magic_link` for customer auth sessions.
- Use `business_create_app_checkout` for Stripe Checkout only when Stripe credentials, success/cancel URLs, plan policy, and authority are explicit. In test mode, checkout and magic-link email requests may create local suppressed receipts but must not be described as live Stripe/Postmark delivery.
- Use `business_record_stripe_webhook` to reconcile checkout completion, subscription lifecycle, entitlements, and revenue. Do not mark payment/subscription state complete without webhook or equivalent receipt evidence.
- Use `business_record_app_usage` for product app usage under the app budget cap.
- When a product surface needs visitor, funnel, or campaign attribution, prefer canonical traffic/analytics tools or runtime endpoints if they exist in `business_registry`. Until those rails exist, record the instrumentation blocker or plan; do not fake visitor counts, conversion rates, CAC, or attribution.
- AI-backed product generation must be server-side and business-scoped. Use the canonical runtime route `POST /api/takyon/apps/<business>/generate`, or a product-owned serverless `/api/generate` route that proxies to the same semantics: app session auth, entitlement/tier context, app-budget precheck, Anthropic server-side credential use, and `business_record_app_usage` receipt. Do not expose provider keys to the browser and do not present a client-side demo/mock as the real feature.
- Frontend auth/account/checkout UI must call the Hermes runtime API or visibly say `DEBUG: <feature> is not wired to Hermes runtime yet`. Do not emulate sessions, customers, entitlements, subscriptions, or checkout in browser storage or hardcoded fixtures.

Do not implement a separate ad hoc auth/payment/subscription store inside product app files when these Hermes app rails fit. Do not use a fixed Takyon visual template as the final product UI. Manual paid entitlements without Stripe/webhook evidence are fake billing state unless they are explicitly non-billing/internal.
