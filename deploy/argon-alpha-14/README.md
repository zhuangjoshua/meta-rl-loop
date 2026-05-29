# argon-alpha-14 deploy config

This directory tracks the VPS Caddy config for `137.184.75.57`.

Apply it with:

```bash
deploy/argon-alpha-14/apply-caddyfile.sh
```

The Caddyfile owns:

- `app.fourmanifold.com` -> Takyon dashboard on `127.0.0.1:9119`
- `research-composer.fourmanifold.com` -> legacy service on `127.0.0.1:9120`
- shared product subdomains through Caddy on-demand TLS
- Caddy's ask gate: `http://127.0.0.1:9119/api/product-tls/ask`

Do not add a Caddy block per new business. New `slug.fourmanifold.com` hosts
should route through the shared product subdomain route and be approved by the
Takyon ask endpoint only when the business exists.
