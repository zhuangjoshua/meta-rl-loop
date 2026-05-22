# Generated Product Backend Robustness

## Meaning Of "Generated App"

When the operator clicks Build Company, the generated app is not just a website.

It must include:
- polished public homepage
- usable product workflow
- generated-app server/API routes where the product needs backend behavior
- generated-app subuser auth
- sessions
- entitlements
- checkout path
- AI gateway calls with per-user limits
- persistence for product outputs where the module requires it
- deployment URL saved only after health checks pass

A frontpage-only result is not a completed generated product.

A generated product backend is not a stub backend template. The template provides contracts, helpers, and tested rails; the product module must contain real coded behavior for its workflow within those rails.

## Backend Ownership

The generated product backend must be mostly platform-owned, not LLM-invented.

Platform-owned pieces:
- generated app auth/session client
- magic-link verification routes
- entitlement lookup
- Stripe checkout and webhook integration
- project AI wallet/proxy key
- AI usage reservation/event recording
- app user tier and limit enforcement
- generated app deploy/health record
- product module API contract

The product module may add bounded product-specific backend logic, but it must call platform SDK/client helpers for auth, payments, AI, limits, and secrets.

## Stub Policy

Do not ship placeholder backend routes that return fake success, canned product outputs, fake users, fake payments, fake AI responses, or fake persistence.

Allowed non-final states:
- `blocked` when a real prerequisite is missing
- `failed` when code/vendor/build/tests fail
- explicit setup-required UI backed by recorded backend state

Not allowed:
- API routes that return success without doing the promised work
- product modules that only render a form and no real action
- fake AI results when the AI gateway is unavailable
- fake paid/free entitlement state
- pretending a product backend is complete because homepage deploy passed

## Claude Agent SDK Role

Claude Agent SDK may be used as a bounded local-worker builder for generated product modules.

It may:
- generate or edit files inside a generated-app workspace
- fill typed product config
- implement bounded product-specific routes/components
- use file tools under a restricted workspace

It may not:
- own the whole generated app architecture
- invent auth/payment/AI infrastructure
- receive raw production vendor secrets
- deploy directly without platform gates
- mark a product ready without tests and health checks
- hide a failed backend behind a good-looking homepage

The SDK is useful because it can act like Claude Code in a controlled local workspace. Robustness comes from the scaffold, contracts, tests, and gates around it.

## Parallelism Rule

The website lane should return first when healthy. Product backend/auth/users/Stripe/AI/X/add-ons run as separate workflow jobs where dependencies allow.

Product readiness depends on product gates. Website readiness depends on website gates. X/social readiness depends on X credentials, policy, rate limits, and vendor receipts. Meta/Sora readiness depends on OpenAI Sora configuration and v0 display-only policy. Community/lead/outreach readiness depends on their own data, policy, and vendor prerequisites.

No lane may pretend another lane completed, and a failed/blocked product backend lane must not block independent add-on lanes.

## Required Product Backend Gates

Before a generated product can be marked ready:
- install succeeds
- typecheck succeeds
- build succeeds
- smoke tests exercise homepage and product workflow
- generated auth/session state works or records a real blocked state
- entitlement checks work or record a real blocked state
- AI gateway request path works with project key and per-subuser metering, or records a real blocked state
- Stripe checkout route exists when paid plans are enabled
- deployed URL health check passes
- `*.fourmanifold` alias/proxy is saved only after health check

If any required gate fails, the job status is `failed` or `blocked`; it is not `completed`.

## Verified Implementation Status - 2026-05-19

Implemented and verified:
- Generated app template includes a product page and `/api/product/run` route.
- Generated app backend calls the platform runtime endpoint instead of inventing auth/payment/AI infrastructure.
- Platform runtime endpoint verifies a project AI proxy key before accepting product runs.
- Product run creates/updates a generated app user and stores a `generated_app_product_runs` row with real input/output and receipt id.
- Generated app source install, typecheck, and build passed locally.
- Platform generated-app auth/session routes are implemented and verified by a targeted worker lane completion.

Explicit non-final states:
- AI gateway provider execution is currently `blocked` and records `project_ai_usage_events`; it does not return fake AI output.
- Generated app deploy/alias/health completed in browser E2E for `https://signalbridge-browser-e2e-20260519.fourmanifold.com`.
- Product module customization beyond the curated generic workflow remains pending.

## Verified Update - 2026-05-19 PT

Implemented and verified:
- Platform Anthropic provider wrapper successfully called `claude-opus-4-7` with adaptive thinking/output effort and returned a real provider request id in local smoke.
- Product-run runtime prompt now requests strict customer-facing JSON and forbids internal Takyon/build/auth/Stripe/X/Meta language in generated product output.
- Product-run output parser now extracts `summary` and `nextSteps` from JSON first and only falls back to cleaned text when provider output is not strict JSON.

Explicit scope:
- The old `takyon-v3-smoke-1779241422123` deployment was not rebuilt in this slice at the operator's request.
- The current template fix applies to newly built/rebuilt generated apps.
