---
name: takyon-build-product
description: Shape or improve a product when a business lacks a usable offer or product surface.
---

# Takyon Build Product

Use this skill when the business has no product, no clear offer, or a product that cannot support distribution yet.

## Practice

- Read the business state and relevant brain files first.
- Define the smallest product or offer that can create evidence.
- Create a product workspace only as needed, such as `product/mvp`, `product/site`, `product/checkout`, or a sharper path.
- Write concise specs, acceptance criteria, copy, and risks into workspace files.
- The app's look is business context, not runtime code. Write or update a business design brief such as `product/design-brief.md`, then record `business_upsert_app_surface_contract` with the design brief, runtime API base, source path, routes, theme source, and constraints.
- For signup/login, product customers, sessions, entitlements, plan policy, checkout, subscriptions, revenue, and app usage budget, use the canonical Hermes app tools (`business_upsert_app_plan`, `business_configure_app_budget`, `business_request_app_magic_link`, `business_create_app_checkout`, `business_record_stripe_webhook`, and related app tools).
- Record guarded build, deploy, or vendor work requests with `business_enqueue_job` only when there is no more specific app tool.
- Record product assumptions and lessons in the business brain when they should affect future CEO decisions.

Do not fake builds, deploys, payments, or vendor work. Do not ship a hardcoded Takyon UI as the product surface. Record the request with required APIs/env vars and only treat it as executed when a concrete receipt exists.
