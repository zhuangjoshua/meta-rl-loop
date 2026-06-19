# Takyon product-site edge worker

Serves built product static (`<slug>.fourmanifold.com`) from the Cloudflare R2
bucket `product-sites` at the edge, and forwards everything else — all `/api/*`
traffic and all reserved operator hosts — to the existing origin (operator Caddy
at `137.184.75.57`) **unchanged**. This takes the VPS out of the static-serving
path without touching the dynamic auth / paywall / usage / entitlements rail.

## What it does, per request

For `*.fourmanifold.com/*` (the Worker route):

1. **Reserved host OR `/api/*`** → forward to the real origin **verbatim**
   (same method, headers, body) and return the origin's response **verbatim**,
   never cached. Reserved hosts: `app`, `skills`, `www`, `admin`, `dashboard`,
   `research-composer` (kept in sync with the `not host …` lists in both
   Caddyfiles). Everything dynamic lives under `/api/*` — product app auth,
   sessions, actions, `generate`, `checkout`, Stripe webhooks, `/api/pty`,
   `/api/events` — so this single prefix is the whole security boundary.

   This is the part that **must not change behaviour**: auth, paywall, usage
   metering and entitlements stay byte-identical to today, and are never cached
   or short-circuited at the edge.

2. **Otherwise (static)** → derive `slug` from the first DNS label, validate it
   against the same grammar as `_safe_product_slug`
   (`hermes-agent-main/takyon_cli/web_server.py:8414`), read the pointer object
   `<slug>/current` from R2 to get the `build_id`, then serve
   `<slug>/<build_id>/<sanitised-path>` from R2. Extension-less unmatched paths
   fall back to `<slug>/<build_id>/index.html` (SPA routing). Requests that look
   like a file (have an extension) get a hard `404` — a missing asset is never
   masked by the HTML shell. Non-`GET`/`HEAD` methods on a product host are
   forwarded to the origin (identical to today's 404/405 behaviour).

### Path safety

The served R2 key is **always** under `<slug>/<build_id>/`. `sanitizeRel`
decodes the path once, rejects malformed `%`-encoding, control chars, `..`
segments and backslashes, and normalises away `.`/empty segments before joining.
There is no way to read outside the build prefix — and the bucket holds only
public dist anyway, so even a hypothetical escape exposes nothing private.

### Caching

- `/assets/*` (content-hashed Vite output) → `public, max-age=31536000, immutable`
- HTML → `public, max-age=60` (so a `<slug>/current` pointer flip is picked up fast)
- other stable-named static → `public, max-age=86400`

Conditional requests (`If-None-Match`) and `Range` are honoured from R2
(`304` / `206`).

## API passthrough — the loop problem and how it's solved

The Worker is bound to `*.fourmanifold.com/*`. A naïve `fetch(request)` for a
`/api/*` request would target the **same** hostname, match the same route, and
**re-invoke this Worker** — an infinite loop (or, with the loop guard, a failure).

**Solution:** forward the subrequest to a **grey-clouded origin hostname** that
is *not* on the Worker route, using `cf.resolveOverride`:

```js
new Request(request, { cf: { resolveOverride: env.ORIGIN_HOST }, redirect: "manual" })
```

`resolveOverride` only changes **where the TCP connection goes**; it does **not**
rewrite the URL or the `Host` header. So:

- The connection is sent to `origin.fourmanifold.com` (→ `137.184.75.57`) instead
  of looping back through the `*.fourmanifold.com/*` Worker route.
- Caddy still sees `Host: <slug>.fourmanifold.com`, so it matches the per-business
  product site block and presents the **Cloudflare origin cert** exactly as today.
- The subrequest still **egresses from Cloudflare's network**, so it arrives at
  Caddy from a Cloudflare IP and passes the `fourmanifold_edge_only` snippet.

### Required DNS record (one-time)

Add a **DNS-only (grey-cloud)** record so the origin is reachable by a name that
is **not** behind this Worker:

| Type | Name                    | Value           | Proxy status      |
|------|-------------------------|-----------------|-------------------|
| A    | `origin.fourmanifold.com` | `137.184.75.57` | **DNS only (grey)** |

- It must be **grey-clouded** so the subrequest hits the VPS directly rather than
  re-entering Cloudflare's proxy/Worker layer.
- Do **not** add a Worker route for `origin.fourmanifold.com` (and the wildcard
  route `*.fourmanifold.com/*` matches `slug` hosts but the Worker only calls out
  to `origin.…` via `resolveOverride`, which bypasses route matching entirely).

### This does NOT expose the origin to non-CF clients

`origin.fourmanifold.com` resolving publicly to the VPS does **not** open a
bypass. Caddy's `fourmanifold_edge_only` snippet `respond 403` for any
`remote_ip` not in the Cloudflare ranges (plus loopback / `10.116.0.0/20`). A
direct client hitting `origin.fourmanifold.com` comes from its own IP — **not** a
Cloudflare IP — and gets `403`. Only Worker subrequests (which egress from CF
IPs) pass. The lockdown is unchanged; we only added a name pointing at the same
already-locked host.

> If you'd rather not publish an origin name at all, the alternative is a
> Cloudflare **Tunnel** (`cloudflared`) bound to a hostname and reached the same
> way. `resolveOverride` + grey-cloud is the lighter option and reuses the
> existing CF-IP lockdown, so it's the default here.

## R2 bucket

Bucket `product-sites` holds **only public built dist**:

```
<slug>/current            # pointer: the live build_id (lowercase hex, 16–64 chars)
<slug>/<build_id>/<rel>   # the built SPA dist for that build
```

Nothing private is ever written here. Private workspace data stays in the
existing Supabase bucket (`<slug>/__takyon/workspace/...`); R2 is publish-only,
dist-only.

Create the bucket once:

```bash
wrangler r2 bucket create product-sites
```

The **publish side** (VPS) writes builds into R2 with an S3-compatible token
(`R2_S3_ENDPOINT` / `R2_S3_ACCESS_KEY_ID` / `R2_S3_SECRET_ACCESS_KEY` /
`R2_BUCKET`, endpoint `https://<cf-account>.r2.cloudflarestorage.com`), resolved
through the env-backed / TK pattern — the same mechanism as the existing
`SUPABASE_S3_*` keys, no more exposed. **The Worker never holds an S3 token**: it
reads R2 through the native binding (`PRODUCT_SITES`), which grants no write
access and no key material.

## Deploy

```bash
cd deploy/cloudflare/product-worker

# one-time: create the bucket and the grey-clouded origin DNS record (above)
wrangler r2 bucket create product-sites

# deploy / update the worker (binds the route + R2 + ORIGIN_HOST var from wrangler.toml)
wrangler deploy

# watch live logs
wrangler tail takyon-product-worker
```

### Exact route binding

`wrangler.toml` declares:

```toml
routes = [
  { pattern = "*.fourmanifold.com/*", zone_name = "fourmanifold.com" },
]
```

One wildcard route covers all product subdomains. Reserved operator hosts also
match it and are handled in-code by forwarding to the origin, so there is no
per-host route to maintain.

### Bindings summary

| Binding        | Kind     | Value             | Notes                                  |
|----------------|----------|-------------------|----------------------------------------|
| `PRODUCT_SITES`| R2 bucket| `product-sites`   | read-only at runtime; public dist only |
| `ORIGIN_HOST`  | var      | `origin.fourmanifold.com` | grey-cloud DNS-only → `137.184.75.57` |

## Rollback

Because the static rail is purely additive at the edge, rollback is a Cloudflare
control-plane action with no VPS change:

- **Disable the Worker route** (or `wrangler delete`) → all traffic falls back to
  the existing Caddy path (`*.fourmanifold.com` → subuser `:9119`
  `_serve_product_site_file`), which still serves static and `/api/*` exactly as
  before. The VPS static path was never removed; it's just bypassed while the
  Worker is live.
