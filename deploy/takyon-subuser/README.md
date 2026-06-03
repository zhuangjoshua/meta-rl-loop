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

The current runtime still serves shared product hosts from
`$TAKYON_HOME/product-sites`, so first boot and deploys must sync the existing
`product-sites` tree from the operator source host until that surface moves to
another canonical backend.

The sub-user host serves only:

- shared `slug.fourmanifold.com` product subdomains
- the narrow app-runtime rails behind those hosts
- the product-host TLS ask gate

It should not serve `app.fourmanifold.com`, dashboard chat, `/api/ws`, or
operator/config/env surfaces.
