# argon-alpha-14 deploy config

This directory tracks the VPS Caddy config for `137.184.75.57`.

Apply it with:

```bash
deploy/argon-alpha-14/apply-caddyfile.sh
```

Bootstrap and deploy the operator host with:

```bash
deploy/argon-alpha-14/bootstrap-host.sh
deploy/argon-alpha-14/deploy-runtime.sh
```

Repair legacy product-service drift on the activation host with:

```bash
deploy/argon-alpha-14/repair-product-runtime.sh
```

The operator plane's tracked runtime contract includes Docker because
`business_claude_agent_task` defaults `product/site` work onto the isolated
Docker rail. `deploy-runtime.sh` now bootstraps/verifies Docker and fails fast
if the operator host cannot run the tracked Claude Agent SDK container image.
The tracked contract now also includes a pinned system-wide `deno` install plus
`systemd-run`, because scheduled product actions execute on this plane through
the shared actions runtime.
Host-level product activation now follows the same least-privilege pattern: the
dashboard/worker stay on the locked-down `takyon` user, and the narrow root-only
systemd/Caddy mutations happen through `takyon-activation-broker.service`
on `127.0.0.1:8012`.

The tracked dashboard systemd unit lives at:

```bash
deploy/argon-alpha-14/takyon-dashboard.service
```

Deploys should install that unit on the VPS before restarting `takyon-dashboard.service`.

This host is the current product activation node (`TAKYON_NODE_NAME=argon-alpha-14`,
`TAKYON_PRODUCT_ACTIVATION_NODE=argon-alpha-14`). Product builds may happen elsewhere later,
but only this host should hold live `product-services/<slug>` trees, write `takyon-product-*.service`
units, and own product-runtime activation.

The Caddyfile owns:

- `app.fourmanifold.com` -> Takyon dashboard on `127.0.0.1:9119`
- `research-composer.fourmanifold.com` -> legacy service on `127.0.0.1:9120`
- explicit legacy static product hosts that still terminate on the operator box
- shared dynamic `slug.fourmanifold.com` product hosts, terminated at the operator edge and proxied over the private VPC to the sub-user host on `10.116.0.3:80`

Do not add a Caddy block per new normal business here. Shared dynamic
`slug.fourmanifold.com` product routing is decided by the product-host matcher
and TLS ask gate here, then served by `deploy/takyon-subuser/Caddyfile` on the
sub-user plane. Normal businesses should not get new per-host blocks on either
box.
