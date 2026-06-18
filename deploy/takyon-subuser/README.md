# takyon-subuser deploy config

This directory tracks the public product-app/runtime host for the sub-user
plane.

Apply the tracked Caddyfile with:

```bash
deploy/takyon-subuser/apply-caddyfile.sh
```

Deploys should install:

- `deploy/takyon-subuser/takyon-subuser.service`
- `deploy/takyon-subuser/Caddyfile`
- `deploy/takyon-subuser/bootstrap-host.sh` on first boot

The tracked runtime contract for this host now includes a pinned system-wide
`deno` install plus `systemd-run`, because product-host action invokes execute
here on the shared app/runtime plane.

The current runtime still serves shared product hosts from
`$TAKYON_HOME/product-sites`, so first boot and deploys must sync the existing
`product-sites` tree from the operator source host until that surface moves to
another canonical backend.

This host is not the product activation node. Its tracked unit sets
`TAKYON_NODE_NAME=takyon-subuser` and `TAKYON_PRODUCT_ACTIVATION_NODE=argon-alpha-14`,
so shared code can tell that live `product-services/<slug>` activation belongs on the
current top-level operator host. If a future builder host needs to publish remotely,
configure `TAKYON_PRODUCT_ACTIVATION_SSH_TARGET` plus an activation SSH key there.

The sub-user host serves only:

- shared `slug.fourmanifold.com` product subdomains
- the narrow app-runtime rails behind those hosts
- the product-host TLS ask gate

Supabase Auth contract for this plane:

- runtime sign-in expects `SUPABASE_URL`
- runtime sign-in expects one of `SUPABASE_PUBLISHABLE_KEY` or `SUPABASE_ANON_KEY`
- runtime sign-in can use optional `SUPABASE_JWT_SECRET` for legacy HS256 fallback, but current
  Supabase-issued asymmetric tokens verify via JWKS
- tracked deploy now validates that contract before restart, so future VPS deploys fail closed if auth config drifts

Shared Postgres schema migrations are owned by the operator deploy rail. `deploy/takyon-subuser/deploy-runtime.sh`
therefore defaults `TAKYON_RUN_DB_MIGRATIONS=0` to avoid replaying heavyweight shared-table DDL from the
still-live app plane; override it only for an intentional emergency/operator-less migration run.

It should not serve `app.fourmanifold.com`, dashboard chat, `/api/ws`, or
operator/config/env surfaces.

The Caddyfile uses `rate_limit` for edge DDoS/abuse control on the shared product plane
(a global per-IP cap plus tighter per-IP zones on product-app auth and metered
actions/generate/checkout). Stock apt `caddy` lacks that module, so
`deploy/shared/ensure-caddy-ratelimit.sh` rebuilds the Caddy binary with
`github.com/mholt/caddy-ratelimit` (xcaddy), run idempotently from both
`bootstrap-host.sh` and `apply-caddyfile.sh` so every current and future host gets it
through the canonical rail before `caddy validate`.
