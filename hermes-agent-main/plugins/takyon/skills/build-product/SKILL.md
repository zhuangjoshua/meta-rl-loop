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
- Queue deterministic build, deploy, checkout, or vendor work with `business_enqueue_job`.
- Record product assumptions and lessons in the business brain when they should affect future CEO decisions.

Do not fake builds, deploys, payments, or vendor work. Queue them with required APIs/env vars and let the runner produce receipts.
