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

It should not serve `app.fourmanifold.com`, dashboard chat, `/api/ws`, or
operator/config/env surfaces.
