# URLs And Domains

## Operator Platform

Primary operator UI:

```text
/dashboard/takyon
/dashboard/companies
/dashboard/companies/[companyId]
/new/takyon
```

## Generated Apps

Canonical generated app:

```text
https://{siteSlug}.fourmanifold
```

Platform proxy fallback:

```text
/c/{siteSlug}
```

## Generated App APIs

Platform-owned:

```text
/api/generated-apps/[slug]/session
/api/generated-apps/[slug]/auth/verify
/api/generated-apps/[slug]/checkout
/api/ai-gateway/messages
```

Generated app deployments should call the platform AI gateway with a project-scoped key, never raw provider keys.

## Operator Auth Boundary

Operator/platform routes on `fourmanifold.com`, `www.fourmanifold.com`, and `app.fourmanifold.com` must be behind Auth0 unless they are explicitly non-operator machine endpoints with their own bearer/signature/project-key gate.

Publicly callable machine/runtime endpoints:
- `/api/health`
- `/api/cron/dispatch`
- `/api/webhooks/stripe`
- `/api/generated-apps/*`
- `/api/payment-links/*`
- `/c/*`
- `/auth/*`

Verified 2026-05-19 PT:
- No-cookie `https://fourmanifold.com/dashboard/takyon` returns a redirect to `https://app.fourmanifold.com/dashboard/takyon`.
- No-cookie `https://app.fourmanifold.com/dashboard/takyon` returns a redirect to `/auth/login`.
- Authenticated in-app browser can load `/dashboard/takyon` and create a company.
