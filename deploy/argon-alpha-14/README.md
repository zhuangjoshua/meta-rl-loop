# argon-alpha-14 deploy config

This directory tracks the VPS Caddy config for `137.184.75.57`.

Apply it with:

```bash
deploy/argon-alpha-14/apply-caddyfile.sh
```

The tracked dashboard systemd unit lives at:

```bash
deploy/argon-alpha-14/takyon-dashboard.service
```

Deploys should install that unit on the VPS before restarting `takyon-dashboard.service`.

The Caddyfile owns:

- `app.fourmanifold.com` -> Takyon dashboard on `127.0.0.1:9119`
- `research-composer.fourmanifold.com` -> legacy service on `127.0.0.1:9120`
- explicit legacy static product hosts that still terminate on the operator box

Do not add a Caddy block per new normal business here. Shared dynamic
`slug.fourmanifold.com` product routing now belongs in
`deploy/takyon-subuser/Caddyfile`, where the product-host ask gate is served by
the sub-user plane.
