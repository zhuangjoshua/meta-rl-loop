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

The sub-user host serves only:

- shared `slug.fourmanifold.com` product subdomains
- the narrow app-runtime rails behind those hosts
- the product-host TLS ask gate

It should not serve `app.fourmanifold.com`, dashboard chat, `/api/ws`, or
operator/config/env surfaces.
