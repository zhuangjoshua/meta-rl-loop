# Generated App Template System

## Decision

Do not use Open Lovable as the v0 builder.

Use a platform-owned template system:
- Next.js generated app base
- shared design tokens
- UI block registry
- homepage section registry
- product module registry
- typed generated app config
- stable platform client for auth, checkout, AI gateway, and usage state

## UI Library Direction

Use a conventional, editable React/Tailwind/shadcn-style component base:
- Tailwind for layout/theme tokens
- Radix/shadcn-style primitives for accessible UI patterns
- lucide icons where icons are needed
- local block registry rather than remote runtime dependency

This is "OpenLovable-like" in output goals, but not in architecture. We want reusable modules, not an external server/sandbox dependency.

## OpenLovable-Informed Builder Mechanics

Borrow:
- strict file-output protocol for generated code
- generated file manifest
- targeted edit mode
- package/import detection
- build validation
- live preview and screenshot checks

Do not borrow:
- blank Vite starter as the quality baseline
- Vercel Sandbox/E2B as v0 production builder
- mock scrape fallback as success
- arbitrary full-app rewrites for small edits

The base template must be curated and verified before the LLM customizes it. See [OPENLOVABLE_RESEARCH.md](./OPENLOVABLE_RESEARCH.md).

## Cached Surface Rule

Cached generated-app surfaces are allowed only as explicit, versioned template keys.

Current verified cache:
- `latexflow-v1`

Rules:
- Cached surfaces may overwrite only customer-facing surface files.
- Cached surfaces may not overwrite deterministic platform rails such as product-run route, platform client, package/config, auth/session/payment/gateway logic, secrets, or deployment files.
- Cached surfaces are not proof that the full product is complete; they accelerate the first public website/product-preview deploy while product/backend improvement lanes continue.
- Operator-requested website edits bypass the cache and use the Claude Agent SDK surface builder.

## Builder Rule

LLM/builder may:
- choose template variant
- fill typed config
- generate bounded product module code
- write copy into structured fields

LLM/builder may not:
- invent payment/auth/AI infrastructure
- bypass platform SDKs
- fake working integrations
- replace the whole app with unreviewed arbitrary code

Generated product backend behavior is part of the template/module contract. A polished homepage without a working or explicitly blocked product backend is not a completed generated app. See [PRODUCT_BACKEND_ROBUSTNESS.md](./PRODUCT_BACKEND_ROBUSTNESS.md).

## Customer-Facing Product Page Rule

Generated app product pages are customer-facing product surfaces.

They must not show:
- Takyon/operator implementation plans
- internal build plans
- backend/auth/Stripe/X/Meta lane language
- prompt or queue state
- "upgrade" controls inside the workflow unless the checkout lane has generated an intentional customer-facing pricing surface

The product page may show:
- the public product action
- customer input fields
- a real saved result
- an explicit setup-required state only when backed by backend status
- a receipt id for a real stored run

Verified 2026-05-19 PT:
- Future generated-app template code removes the product-page upgrade control and replaces internal brief labels with customer-facing workflow copy.
- Existing smoke deployments are not automatically rewritten by this template change; rebuilding/deploying a generated app is required to pick it up.
