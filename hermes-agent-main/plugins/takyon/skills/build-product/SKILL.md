---
name: takyon-build-product
description: Shape or improve a product when a business lacks a usable offer or product surface.
---

# Takyon Build Product

Use this skill when the business has no product, no clear offer, or a product that cannot support distribution yet.

## Practice

- Read the business state and relevant brain files first.
- Define the smallest product or offer that can create evidence.
- Physical subject matter does not imply physical fulfillment. Unless physical fulfillment is explicit, preserve the operator's intent through a lawful software-native product around the real-world subject.
- Create a product workspace only as needed, such as `product/mvp`, `product/site`, `product/checkout`, or a sharper path.
- Write concise specs, acceptance criteria, copy, and risks into workspace files. When the operator asked to build or publish a product/website, or when an operational `/create`/build/setup bootstrap reaches product after research, also create the actual source files under the declared source path; a `website.md` or design brief is not a built product surface.
- The app's look is business context, not runtime code. Write or update a business design brief such as `product/design-brief.md`, then record `business_upsert_app_surface_contract` with the design brief, runtime API base, source path, routes, theme source, constraints, publish target, and publish policy.
- For signup/login, product customers, sessions, entitlements, plan policy, checkout, subscriptions, revenue, and app usage budget, use the canonical Hermes app tools (`business_upsert_app_plan`, `business_configure_app_budget`, `business_request_app_magic_link`, `business_create_app_checkout`, `business_record_stripe_webhook`, and related app tools).
- For AI-backed product actions, build a real server-side route, not a client-side demo. Prefer the canonical runtime endpoint `POST /api/takyon/apps/<business>/generate`; if the product stack needs its own serverless `/api/generate`, make it a thin business-scoped route with app-session auth, entitlement context, app-budget checks, server-held Anthropic credentials, and a `business_record_app_usage` receipt.
- Never fake product behavior. If auth, checkout, entitlements, provider calls, metrics, or deploy are not wired to canonical Hermes runtime endpoints/tools, the product surface must show a visible `DEBUG`/blocked state or omit the feature. Do not simulate business reality with localStorage sessions, demo query params, hardcoded users, fake checkout URLs, or fake subscription state.
- Record guarded build, deploy, or vendor work requests with `business_enqueue_job` only when there is no more specific app tool. In test mode, product and website publication are allowed when the normal path, budget, credential, and receipt/job gates pass; otherwise build the local product surface and record the exact blocker. If using `business_upsert_app_surface_contract(source_path=...)`, ensure that path contains real app/site source and that `business_verify_product_surface` returns verified and published, or an exact blocker, before reporting the surface as complete.
- Product source may need different runtimes or package managers depending on the stack. Use `business_check_runtime_capabilities` and verification receipts to identify missing capabilities precisely; provision through guarded setup when allowed, or record an exact blocker. Do not collapse all build problems into Node/npm, and do not substitute a stub for a buildable product when the missing capability can be repaired.
- Treat product verification as evidence, not a fixed workflow stage. If verification fails, repair once when the fix is bounded and obvious; otherwise record the blocker and the product path so the CEO can decide the next move.
- Record product assumptions and lessons in the business brain when they should affect future CEO decisions.

Do not fake builds, deploys, payments, auth, sessions, users, entitlements, billing, metrics, provider calls, or vendor work. Do not ship a hardcoded Takyon UI as the product surface. Record the request with required APIs/env vars and only treat it as executed when a concrete receipt exists. Test mode suppresses outreach/acquisition/payment/email delivery, not product/website publication.
