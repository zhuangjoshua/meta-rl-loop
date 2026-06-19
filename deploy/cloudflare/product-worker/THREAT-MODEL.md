# R2 Edge-Static Migration — Threat Model

**Goal.** Serve product static (`<slug>.fourmanifold.com`, the built Vite SPA) from a
Cloudflare R2 bucket at the edge, removing the VPS from the static path. `/api/*` for
the same hostname MUST keep reaching the existing subuser runtime (`:9119`,
`takyon_cli/web_server.py`) **untouched** — auth, magic-link sessions, paywall,
usage metering, entitlements, checkout, and webhook reconciliation stay exactly where
they are, are never cached, and can never be bypassed.

**Adversary posture (operator, verbatim spirit):** *"users are evil, don't compromise
security, don't make API keys more exposed, don't let them bypass usage."* Every
control below is written against an attacker who fully controls their browser, can set
any header on a direct origin request, knows our slug/build-id formats, and will probe
for cross-tenant reads, free inference, and credential leaks.

This document enumerates the attack surface and the **exact guard** for each. The
ordered, reversible deploy steps are in `RUNBOOK.md`.

---

## 0. Target architecture (what changes, what does not)

Today (all on the VPS path):

```
client → Cloudflare edge (CF IPs; origin TLS; edge-only lockdown)
       → operator Caddy 137.184.75.57  (deploy/argon-alpha-14/Caddyfile)
       → product host → reverse_proxy 10.116.0.3:80  (subuser plane)
       → subuser Caddy (deploy/takyon-subuser/Caddyfile)
       → subuser :9119 Python web_server
            ├─ STATIC: _serve_product_site_file  (web_server.py:8684)
            └─ /api/takyon/apps/<slug>/*  (auth, sessions, actions, generate, checkout)
```

After (static served at the edge, API unchanged):

```
client → Cloudflare edge
       ├─ STATIC (everything NOT /api/*) → Cloudflare Worker → R2 binding → 'product-sites'
       └─ /api/*  → origin (operator Caddy → subuser :9119)   ← byte-for-byte unchanged
```

Two storage buckets, two trust classes — **never merged**:

| Bucket | Project / host | Key layout | Trust class |
|---|---|---|---|
| `business-workspaces` (Supabase, `ddftvmjpfghfrdxhavvp`) | existing | `<slug>/__takyon/builds/<build_id>/<rel>` **AND** `<slug>/__takyon/workspace/cas/<digest>`, `<slug>/__takyon/workspace/manifests/<rev>.json` | **MIXED**: built sites co-tenant with **private** source/manifests under one `<slug>/` prefix |
| `product-sites` (Cloudflare R2, new) | new | `<slug>/<build_id>/<rel>` + `<slug>/current` (pointer body = build_id) | **PUBLIC-ONLY**: built sites + pointer, nothing private, ever |

**The single most important invariant of this whole migration:** the public edge read
path (Worker + R2 binding) is bound to `product-sites` and **only** `product-sites`. It
has no credential, binding, or code path that can reach `business-workspaces`. The
reason `business-workspaces` cannot be the edge bucket is structural, not policy: it
co-tenants `<slug>/__takyon/workspace/cas/<digest>` (the **private** committed product
source) under the same `<slug>/` prefix as the built site. A read-only public Worker on
that bucket would be one crafted key away from serving a competitor's source tree.

---

## 1. Cross-tenant / private-data reads from `<slug>.fourmanifold.com`

> Attacker goal: from a product hostname, read **another business's** built site, or
> **this** business's **private** workspace source (CAS blobs, manifests, server-side
> config, `.env`-style files committed to the workspace).

### 1.1 Bucket separation (root guard — defeats the entire class)

* **Attack.** Reuse `business-workspaces` as the edge bucket. The Worker reads
  `victim/__takyon/workspace/cas/<digest>` or `victim/__takyon/workspace/manifests/0.json`
  and serves a competitor's React/TS source, server config, or committed secrets to the
  public internet. Even an "only serve `__takyon/builds/`" prefix filter is one Worker
  bug / one regex slip from a private read.
* **Guard.** Publish to a **separate R2 bucket `product-sites`** whose key space is
  `<slug>/<build_id>/<rel>` and `<slug>/current` — **dist output and pointer only,
  nothing under `__takyon/workspace/`, ever.** The publish mirror (§3.1) copies **only**
  the build artifact `<slug>/__takyon/builds/<build_id>/<rel>` → `product-sites:<slug>/<build_id>/<rel>`.
  Private CAS/manifests are **never** an input to the mirror. The Worker's R2 binding is
  `product-sites` only; it has no handle to `business-workspaces`. Verify the bucket
  contains zero `__takyon/` keys (§ RUNBOOK backfill verify).

### 1.2 Slug spoofing via the `Host` header / key prefix

* **Attack.** Send `Host: ../victim.fourmanifold.com`, `Host: victim.fourmanifold.com.attacker.com`,
  `Host: VICTIM.fourmanifold.com`, `Host: victim..fourmanifold.com`, or a Host with an
  embedded `/` or `%2e%2e`, hoping the Worker derives the R2 key prefix from raw Host and
  lands on `victim/...` or escapes its own prefix.
* **Guard.** The Worker derives the slug with the **same discipline as the runtime**:
  strip the configured base domain (`fourmanifold.com`), lowercase, then validate against
  the exact runtime regex
  `^[a-z0-9][a-z0-9-]{0,78}[a-z0-9]$` OR `^[a-z0-9]$`
  (mirror of `_safe_product_slug`, web_server.py:8414). Anything that fails → **404**, no
  R2 read attempted. The slug is the **only** source of the key prefix; the URL path
  contributes only the `<rel>` suffix (§1.3). A validated slug cannot contain `/`, `.`,
  `..`, `%`, or uppercase, so it cannot traverse out of `<slug>/`. Reserved subdomains
  (`app`, `skills`, `www`, `admin`, `dashboard`, `research-composer`) must be **excluded**
  (mirror of `_is_reserved_public_subdomain`) so they are never treated as product slugs —
  though those hosts are not routed to the Worker in the first place (§2.4).

### 1.3 Path traversal in `<rel>` (the asset path)

* **Attack.** Request `GET /../victim/current`, `/%2e%2e/victim/index.html`,
  `/..%2f..%2fvictim%2fbuild/app.js`, `/foo/../../bar`, a backslash variant
  `/..\victim`, or a doubly-encoded `%252e%252e` to climb out of `<slug>/<build_id>/`
  into another tenant's prefix or into `<slug>/current`.
* **Guard.** Normalize and reject in the Worker, mirroring `_safe_rel` (storage.py:222)
  and the FileResponse containment check (`root in (target, *target.parents)`,
  web_server.py:8745): decode percent-encoding **once**, replace `\` with `/`, split on
  `/`, and reject if any segment is empty, `.`, or `..`. The R2 key is built **only** by
  string-joining `<slug>` + `<build_id>` + sanitized `<rel>`; never pass attacker bytes
  to a path resolver. Empty path → `index.html`. SPA fallback (unknown non-asset path →
  `<slug>/<build_id>/index.html`) must use the **already-resolved** build_id, not a path
  derived value. (R2's flat keyspace has no real `..` semantics, but a Worker that
  string-concatenates a raw path can still synthesize `victim/...` — reject before the
  `.get()`.)

### 1.4 build_id guessing / forcing a stale or foreign build

* **Attack.** Request `/<known-or-guessed-build-id>/index.html` directly, or set a header/
  query the Worker might honor, to (a) serve a **different business's** build that happens
  to share R2 with this one, or (b) pin a victim to an old, vulnerable, or unpublished
  build, or (c) read a build that was never promoted to live.
* **Guard.** **The client never chooses the build_id.** The Worker resolves the live
  build_id **server-side** from the `<slug>/current` pointer object in R2 (read-through,
  short TTL). The URL path only ever contributes `<rel>` within the resolved
  `<slug>/<build_id>/` prefix. build_id format is validated `^[0-9a-f]{16,64}$` (mirror of
  `build_object_prefix`, storage.py:393) on the value read from `current` — a corrupt
  pointer fails closed (404/503), never serves an arbitrary prefix. Because the pointer is
  per-slug and the prefix is `<slug>/<build_id>/`, even a correctly-guessed foreign
  build_id cannot be reached: it would have to live under the **victim's** slug prefix, and
  the slug is pinned by Host (§1.2). Guessing is also infeasible: build_id is a
  ≥16-hex-char content/random id.

### 1.5 Pointer poisoning (`<slug>/current`)

* **Attack.** Get the `<slug>/current` object to point at a foreign or malicious build_id —
  e.g. by abusing a write path, a Worker that accepts a client-supplied pointer, or a race
  during publish — so the victim's live site serves attacker content or another tenant's
  build.
* **Guard.** `current` is **write-only by the publish mirror** (VPS, authenticated R2 S3
  write token, §3.1). The Worker's R2 binding is **read-only** (or the Worker code never
  issues a write); no client request can `PUT` `current`. The pointer is written **last**
  in the publish sequence (after all `<slug>/<build_id>/<rel>` objects land) and contains
  **only** a validated `^[0-9a-f]{16,64}$` build_id under the **same** `<slug>/` prefix it
  governs — the mirror never writes a cross-slug build_id into a slug's pointer (it copies
  `live_build_id` for **that** business out of `app_surface_contracts`, the same indexed
  source the runtime uses in `live_build_pointer`, core.py:881). Source of truth for "what
  is live" remains the Postgres `app_surface_contracts.live_build_id`; R2 `current` is a
  derived mirror, and a divergence is resolved by re-running the mirror, never by trusting
  R2 over Postgres.

### 1.6 Cache key confusion / cross-tenant cache poisoning at the edge

* **Attack.** Cause Cloudflare's edge cache to return business A's asset for a request to
  business B — e.g. if the cache key omits the hostname, or if a shared path like
  `/index.html` or `/assets/app.js` keys identically across tenants.
* **Guard.** The Worker reads from R2 by the **fully-qualified, slug-scoped key**
  `<slug>/<build_id>/<rel>`; the build_id segment is content/deploy-unique per business, so
  two tenants never share an R2 key. If the Worker uses the Cache API or relies on
  Cloudflare's HTTP cache, the cache key **must include the Host** (default zone behavior
  keys on full URL including host; if a custom `cache.match`/`caches.default` is used, the
  cache key Request MUST carry the original Host or a synthetic key embedding `<slug>`).
  HTML is served `Cache-Control: no-cache` / not edge-cached (mirror of
  `_product_site_file_response`, web_server.py:8660) so a pointer flip is picked up
  immediately; only content-hashed assets (`-HASH.ext`, immutable) and stable-named static
  get TTLs — and those are build_id-scoped so they can't collide across tenants.

### 1.7 Listing / enumeration of the bucket

* **Attack.** Hit the R2 public bucket URL or a Worker route that proxies `LIST` to
  enumerate every `<slug>` and pull competitor sites wholesale.
* **Guard.** R2 bucket is **not** exposed via an R2 public bucket URL (`r2.dev` / custom
  public domain) — access is **only** through the Worker, which performs **point GETs** by
  computed key and **never** exposes `list()`. There is no Worker route that returns a key
  listing. (Even if a public dev URL were briefly enabled, slugs are public anyway —
  they're the hostnames — but listing would leak the existence of unpublished/paused
  businesses, so it stays off.)

---

## 2. /api auth / paywall / usage bypass introduced by moving serving to the edge

> Attacker goal: get **free or unauthenticated** access to metered/paid/private API
> behavior — `/api/takyon/apps/<slug>/{auth,sessions,actions,generate,checkout}` — by
> exploiting the new edge split.

### 2.1 `/api/*` accidentally served/cached by the Worker (the cardinal sin)

* **Attack.** The Worker matches too broadly (`*`) and serves `/api/...` from R2 (404s, or
  worse, serves a **cached** prior API response), so auth/usage never runs — free
  inference, or a stale authenticated payload served to an anonymous client.
* **Guard.** The Worker route is **scoped to static only**. Cloudflare route binding must
  **exclude** `/api/*` — bind the Worker to the product host but configure the route so any
  request whose path starts with `/api/` is **passed to origin** (either a separate route
  `*.fourmanifold.com/api/*` mapped to origin/no-worker that is **more specific** and
  therefore wins, or an explicit first-line check in the Worker: `if
  url.pathname.startsWith('/api/') → fetch(origin)` / `return env.ORIGIN.fetch(request)`
  with **no caching**). Defense in depth: the Worker **never** caches or serves anything
  under `/api/`, `/auth/`, `/billing/`, `/api/webhooks/`, or `/api/pty` (WebSocket). A
  regression test / smoke check (RUNBOOK) asserts `/api/takyon/apps/<slug>/auth/...` from
  the edge returns the **runtime's** response headers, not a Worker/R2 response.

### 2.2 API responses cached with auth (stale-auth / cross-user leak)

* **Attack.** The Worker (or a misconfigured Cloudflare cache rule) caches a response that
  carried a `Set-Cookie`, `Authorization`-scoped body, or per-customer entitlement JSON, and
  later serves it to a different customer — leaking a session or a paid result for free.
* **Guard.** Belt: §2.1 means the Worker never touches `/api/*` at all, so no API response
  enters the Worker cache. Suspenders: the edge cache config for the product host must
  **respect origin `Cache-Control`** and **never cache responses with `Set-Cookie`** (CF
  default: responses with `Set-Cookie` are not cached; do not override with a blanket
  "cache everything" page rule). The runtime already returns `no-store`/`no-cache` on
  dynamic/API responses (`_product_site_unavailable_response`,
  `_product_site_file_response`). No Cloudflare "Cache Everything" page rule may be applied
  to `*.fourmanifold.com`.

### 2.3 `/api/*` not reaching the runtime (availability = soft bypass / outage)

* **Attack.** Not a read attack but a correctness one: after the split, the Worker
  swallows `/api/*` (returns 404 from R2) or the origin route is removed, so the **paid app
  stops working** — or, worse, a half-broken state where static loads but checkout/auth
  silently fail, which an attacker could exploit to confuse users or where a misfire
  fails *open*.
* **Guard.** `/api/*` continues to route to origin **unchanged** (§2.1). The origin path
  `Cloudflare → operator Caddy → 10.116.0.3:80 → subuser :9119` is **not modified** by this
  migration. Verify after **every** cutover step that `/api/takyon/apps/<slug>/...` returns
  a live runtime response (RUNBOOK per-step verify). Fail posture is **closed**: if the
  edge can't reach origin for `/api`, the request errors — it does **not** fall back to a
  cached or Worker-synthesized success.

### 2.4 Host / reserved-host confusion routing API to the wrong plane

* **Attack.** Use `app.fourmanifold.com`, `skills.…`, `admin.…`, or a crafted Host to make
  the Worker treat an **operator/control-plane** host as a product host (and serve its
  static from R2, or worse proxy its `/api` oddly), reaching the operator dashboard or
  control plane through the product edge.
* **Guard.** The Worker route binds **only** to product hosts. Reserved hosts
  (`app`/`skills`/`www`/`admin`/`dashboard`/`research-composer`.fourmanifold.com) are
  **excluded** from the Worker route exactly as the Caddyfiles exclude them from the
  `@product` matcher. `app.fourmanifold.com` continues to go **operator Caddy → :9119
  dashboard** with **no Worker** in front of its `/api/*`. The Worker also re-validates the
  slug (§1.2) and treats a reserved/invalid slug as 404, so even a mis-scoped route fails
  closed rather than serving control-plane bytes.

### 2.5 CORS / cookie / Host changes that weaken the session protocol

* **Attack.** The edge rewrites `Host`, drops `CF-Connecting-IP`/`X-Forwarded-For`, changes
  the apparent `Origin`, or alters cookie scope, so that (a) magic-link/session cookies set
  by the runtime for `<slug>.fourmanifold.com` no longer bind correctly, (b) the runtime's
  per-IP rate limits (subuser Caddy `product_auth`/`product_actions` zones keyed on
  `{client_ip}`) all collapse to one IP and stop limiting, or (c) a permissive CORS
  response lets a third-party origin replay credentialed API calls.
* **Guard.** For `/api/*` **nothing changes** — those requests bypass the Worker and ride
  the **existing** origin path, which already preserves `Host {host}` and
  `X-Forwarded-Proto https` (Caddyfiles) and derives client IP from
  `CF-Connecting-IP`/`X-Forwarded-For` via `trusted_proxies_strict`
  (`client_ip_headers CF-Connecting-IP X-Forwarded-For`). The Worker, which handles
  **only** static GETs with no credentials, must **not** add permissive CORS
  (`Access-Control-Allow-Origin: *` with credentials) to anything, and must not set or
  reflect cookies. Cookies/session are owned solely by the runtime on the unchanged `/api`
  path; the Worker never participates in the session protocol. Cross-check that the
  product host still passes `CF-Connecting-IP` to the subuser Caddy so the
  `product_auth`/`product_actions`/`product_all` zones keep limiting per real client IP.

### 2.6 The loop hazard (edge → origin → edge)

* **Attack / footgun.** A grey-cloud (DNS-only) origin record plus a Worker route, or an
  origin that itself proxies back through Cloudflare, creates an infinite request loop
  (Worker fetches origin which is the same CF zone which re-invokes the Worker), causing
  522/loop errors and a self-DoS — and during the confusion, a fail-open window.
* **Guard.** The Worker's origin fetch for `/api/*` targets the **origin** explicitly — use
  a dedicated origin hostname / route that is **not** matched by the Worker (e.g. a
  separate `*.fourmanifold.com/api/*` route to origin that the Worker does not own), or
  fetch the origin IP/origin-hostname directly with `resolveOverride`/a service binding so
  the subrequest does not re-enter the Worker route. The origin (operator + subuser Caddy)
  must **not** wrap its upstream back through Cloudflare. Verify post-cutover that an
  `/api` request makes exactly **one** origin hop (check `Server`/`Via` headers and that
  there's no 522/loop). Static requests never fetch origin, so the loop can only arise on
  the `/api` passthrough — keep that passthrough a single, explicit, non-recursive route.

### 2.7 Usage/credit gates live behind `/api` only — confirm none moved to static

* **Attack.** Assume some metered capability was being enforced by *serving* (e.g. an asset
  gate) and is now lost at the edge.
* **Guard.** Confirmed by construction: **all** money/auth gates live in the runtime under
  `/api/takyon/apps/<slug>/{auth,sessions,actions,generate,checkout}` (reserve→settle→
  release usage rail, `app_usage.py`; creative credits; entitlements; Stripe webhook
  reconciliation). The static site is **inert** — JS/CSS/HTML with no server authority.
  Moving inert static to the edge removes **zero** gates. The Worker must not be given any
  capability (no env secret usable for inference, no provider key, no DB) that could
  constitute an ungated paid path — it is a dumb file server for `product-sites` only.

---

## 3. Credential exposure (must be **no more exposed** than `SUPABASE_S3_*` today)

> Attacker goal: extract an R2 token (read or write) and use it to read/write the bucket
> directly, escalate to `business-workspaces`, or pull a provider key.

### 3.1 R2 **write** token on the VPS (publish side)

* **Status quo to match.** Today the VPS resolves `SUPABASE_S3_ACCESS_KEY_ID` /
  `SUPABASE_S3_SECRET_ACCESS_KEY` via `_sensitive_config_value` → **safebox / TK**
  (storage.py:171, 700). They are **not** in the business runtime's `os.environ`; the
  business-tool/skill layer never reads them raw. The CLAUDE.md TK rule applies.
* **Guard.** The R2 S3 **write** credentials (`R2_S3_ACCESS_KEY_ID`,
  `R2_S3_SECRET_ACCESS_KEY`, plus non-secret `R2_S3_ENDPOINT`
  `https://<cf-account>.r2.cloudflarestorage.com`, `R2_BUCKET=product-sites`) are
  resolved the **same way** — secrets via `_sensitive_config_value` (→ safebox/TK), the
  non-secret endpoint/bucket via `_env_backed_config_value` — by a publish-mirror backend
  that mirrors `SupabaseS3StorageBackend` (storage.py:679) and fails closed
  (`StorageUnconfigured`) when any cred is missing. **No more exposed than
  `SUPABASE_S3_*`:** same host, same resolution helper, same boto3 client pattern, same
  fail-closed posture. The R2 **API token** is **scoped to the `product-sites` bucket
  only** (Object Read & Write, that bucket), so even a leaked write token **cannot** touch
  `business-workspaces` or any other R2 bucket, and cannot read provider keys. The token is
  never written into client JS, the dist, the Worker, the Caddyfile, or a committed file.

### 3.2 R2 **read** binding (Worker side) — no secret at all

* **Guard.** The Worker reads R2 via a **native R2 binding** (`env.PRODUCT_SITES.get(key)`),
  **not** an S3 access key. A binding carries **no extractable secret**: there is no
  `AWS_ACCESS_KEY_ID` in the Worker env, nothing to leak in a stack trace, nothing shippable
  to the browser. The binding is **read-only-by-use** (the Worker never calls `.put`/`.delete`)
  and is bound to `product-sites` only — it cannot name `business-workspaces`. This is
  **strictly less** credential exposure than today's static path (which runs through a VPS
  process that *does* hold the Supabase S3 secret).

### 3.3 No secret in client JS / the dist

* **Attack.** A build embeds an R2 key, the safebox token, or a provider key into the
  shipped JS, and it's now world-readable from the public bucket.
* **Guard.** R2 creds (read binding, write token) live **only** in Cloudflare Worker
  bindings (read) and VPS safebox/TK (write). They are **never** referenced in product
  source, never injected at build time, never in `product/` artifacts. The publish mirror
  uploads **exactly** the existing dist bytes (`<slug>/__takyon/builds/<build_id>/<rel>`)
  with **no** credential injection. The pre-publish scanner posture (no node_modules, no
  `.env`, no source maps that leak server config) is unchanged — and is now **more**
  important because the dist is world-served from R2: confirm the build artifact contains
  no `.env`, no `*.key`, no server-only config (RUNBOOK backfill verify greps the uploaded
  prefix).

### 3.4 Token blast radius / rotation

* **Guard.** The R2 write token is a **distinct** credential from `SUPABASE_S3_*` and from
  every provider key; revoking it disables only publishing-to-R2 (static serving from
  already-published builds continues via the read binding) and touches nothing else.
  Rotation = mint a new bucket-scoped token in Cloudflare, write it to TK/safebox, restart
  the publish path. No provider key, no Supabase key, and no Postgres credential is
  reachable from either R2 token.

---

## 4. Origin-lockdown preservation (edge-only Caddy must still block non-CF clients)

> Attacker goal: hit the VPS origin **directly** (bypassing Cloudflare's WAF, rate limits,
> and — for `/api` — any edge control), or reach the runtime over a grey/DNS-only record
> added for the migration.

### 4.1 The existing lockdown must not regress

* **Status quo.** Both Caddyfiles enforce `(fourmanifold_edge_only)`: any `remote_ip`
  **not** in the Cloudflare CF IP ranges (or loopback `127.0.0.1/8 ::1/128` or the internal
  `10.116.0.0/20`) gets `403 "forbidden"`, with `trusted_proxies_strict` + `client_ip_headers
  CF-Connecting-IP X-Forwarded-For` so the real client IP is derived only from CF-trusted
  headers. Origin TLS is the Cloudflare origin cert.
* **Guard.** This migration **does not touch** the edge-only snippet or the CF IP list.
  After adding any DNS record for the migration, re-verify that a **direct** request to the
  VPS IP (`curl --resolve <slug>.fourmanifold.com:443:137.184.75.57 ...` from a non-CF IP)
  still returns `403 forbidden`, for **both** static and `/api` paths. The lockdown is the
  reason an attacker cannot skip the edge and hit `:9119` directly.

### 4.2 The grey-cloud origin record hazard

* **Attack.** To give the Worker an origin to fetch for `/api`, someone adds a **grey-cloud
  (DNS-only)** A record (e.g. `origin.fourmanifold.com` → `137.184.75.57`) that is **not**
  proxied by Cloudflare. An attacker who discovers that hostname now reaches the VPS
  **directly**, bypassing the CF edge entirely — and because it's DNS-only, the
  `(fourmanifold_edge_only)` allowlist is the **only** thing standing between them and the
  runtime.
* **Guard.** Prefer **no** new grey record: route the Worker's `/api` passthrough to the
  **existing proxied** product host / a Cloudflare **service binding** so the origin fetch
  still rides the orange-cloud edge (and stays inside the CF IP allowlist). **If** an
  origin-pull hostname is unavoidable, it must (a) resolve to the VPS only, (b) be covered
  by the **same** `(fourmanifold_edge_only)` 403 allowlist in Caddy (so a direct hit from a
  non-CF IP is still `403`), and (c) carry the Cloudflare origin TLS cert. Verify: from a
  non-CF IP, the grey hostname returns `403`. The allowlist already includes the CF ranges,
  loopback, and `10.116.0.0/20`; a grey record does **not** widen it — direct non-CF
  clients stay blocked. Never add an IP to the allowlist to "make the grey record work."

### 4.3 The Worker→origin subrequest must remain inside the trust boundary

* **Guard.** When the Worker fetches origin for `/api`, the subrequest originates from
  Cloudflare's network (CF egress IPs), so it lands inside the `(fourmanifold_edge_only)`
  allowlist and Caddy accepts it — exactly as a normal edge→origin request does today. The
  Worker must forward the original `Host` and let CF set `CF-Connecting-IP` to the real
  client so per-IP rate limits (§2.5) keep working. It must **not** spoof
  `CF-Connecting-IP`/`X-Forwarded-For` (Caddy's `trusted_proxies_strict` only trusts CF
  ranges; a forged client_ip header from outside CF is ignored, but the Worker should pass
  the genuine value).

---

## 5. Cutover safety + instant rollback

> Goal: every step is reversible; a single route-binding change reverts to the
> known-good VPS static path with no data migration and no rebuild.

### 5.1 The static path is **additive** until the final step

* The R2 bucket, publish mirror, backfill, and Worker are all stood up **while the VPS
  static path keeps serving** (`_serve_product_site_file` is untouched). Nothing is removed
  from the VPS until the edge path is verified on a **fresh** business (RUNBOOK final steps).
  So at every intermediate step, rollback = "do nothing / unbind the Worker," and the VPS
  still serves.

### 5.2 One test business first, then `*`

* The Worker route is bound to **one test business's host** first
  (`<test-slug>.fourmanifold.com`), verified end to end (static-from-R2 **and**
  `/api`-still-works **and** sign-in **and** a test checkout intent), **before** the
  wildcard `*.fourmanifold.com` route is bound. A failure on the test host affects **one**
  business and is reverted by unbinding that one route.

### 5.3 Instant rollback = unbind the route

* **Static breakage rollback.** Disable/unbind the Worker route on the affected host(s) in
  Cloudflare. With the Worker gone, `*.fourmanifold.com` static falls straight back to the
  **unchanged** origin path (operator Caddy → subuser :9119 → `_serve_product_site_file`).
  No rebuild, no republish, no DNS change, no VPS change. This is a **single dashboard/API
  action** and propagates in seconds.
* **API breakage rollback.** Because `/api/*` was never moved, an `/api` failure is by
  definition **not** caused by the Worker; the rollback for any `/api` regression is the
  same route-unbind (proves the edge split is the cause) plus reverting whatever route/DNS
  edit introduced it. The origin `/api` path can always be reached by removing the Worker.
* **Credential/grey-record rollback.** Revoking the R2 write token stops publishing but
  not serving; deleting any grey origin record removes that direct-hit surface (and the VPS
  edge-only lockdown still protected it while it existed).

### 5.4 Keep VPS materialization until the very end

* `_serve_product_site_file` + the Supabase build artifact + the on-VPS materialization are
  the **rollback target**. They are removed **only** as the **last** RUNBOOK step, **after**
  a fresh-business E2E passes on the edge path, and the removal is itself reversible (the
  code path and Supabase data are not deleted destructively in the same step — disable
  first, delete later).

### 5.5 Fail-closed defaults during cutover

* If R2 is unreachable, the pointer is missing, or the build isn't backfilled, the Worker
  returns **404/503 with `no-store`** (mirroring `_product_site_unavailable_response`) —
  it does **not** fail open to a foreign build or to a cached cross-tenant asset. A 503 on
  static is a clean, reversible signal to roll back; it never becomes a security event.

---

## 6. Guard checklist (one line each — every item is verified in RUNBOOK)

1. Separate R2 bucket `product-sites`; zero `__takyon/workspace/` keys; Worker binding is `product-sites` only. (§1.1)
2. Worker validates slug against the exact `_safe_product_slug` regex; reserved hosts excluded. (§1.2, §2.4)
3. Worker sanitizes `<rel>` (decode-once, reject empty/`.`/`..`/`\`); key = join(slug, build_id, rel) only. (§1.3)
4. build_id never client-chosen; resolved from `<slug>/current`; validated `^[0-9a-f]{16,64}$`. (§1.4)
5. `current` written only by the VPS publish mirror, last, same-slug, from Postgres `live_build_id`. (§1.5)
6. Edge cache key includes Host; HTML `no-cache`; hashed assets immutable; no cross-tenant key collision. (§1.6)
7. No R2 public bucket URL; no Worker `list()`; point GETs only. (§1.7)
8. Worker route excludes `/api/*`; `/api` rides the unchanged origin path; Worker never caches/serves `/api`. (§2.1, §2.3)
9. No "Cache Everything" rule; `Set-Cookie` responses never cached; runtime `no-store` respected. (§2.2)
10. Worker adds no permissive CORS, sets no cookies; `Host`/`CF-Connecting-IP` preserved to origin for `/api`. (§2.5)
11. `/api` passthrough is a single, non-recursive origin hop (no Worker re-entry / 522 loop). (§2.6)
12. Worker holds no provider key / DB / inference capability — it is a dumb `product-sites` file server. (§2.7)
13. R2 write token via safebox/TK (`_sensitive_config_value`), bucket-scoped to `product-sites`, fail-closed. (§3.1)
14. Worker uses a native R2 read binding (no S3 secret in Worker env); read-only-by-use. (§3.2)
15. No R2 cred / safebox token / provider key in client JS or the dist; uploaded prefix greps clean. (§3.3)
16. Edge-only Caddy 403 allowlist unchanged; direct non-CF hit to VPS still `403` for static and `/api`. (§4.1)
17. Prefer no grey origin record; if any, it's allowlist-covered + origin-TLS + verified `403` from non-CF. (§4.2)
18. Worker→origin subrequest stays inside CF trust boundary; real client IP preserved for rate limits. (§4.3)
19. Bind one test host first; verify; then `*`; rollback = unbind route (single action, seconds). (§5.2, §5.3)
20. VPS materialization removed only as the last step, after fresh-business E2E, reversibly. (§5.4)
