# R2 Edge-Static Migration — RUNBOOK

Ordered, **reversible** deploy steps to move product static
(`<slug>.fourmanifold.com`) onto a Cloudflare R2 bucket served by a Cloudflare
Worker, while `/api/*` stays on the **unchanged** VPS runtime path. Read
`THREAT-MODEL.md` first — every step here exists to satisfy a guard there, and
each step cites the guard it verifies.

**Invariants enforced at every step**

* The VPS static path (`_serve_product_site_file`, `takyon_cli/web_server.py:8684`)
  and the Supabase build artifact stay **live until the final step** — they are the
  rollback target.
* `/api/*` is **never** moved, cached, or proxied through the Worker.
* No step is destructive; each has an explicit revert.
* New code follows the existing seams: a publish-mirror R2 backend mirrors
  `SupabaseS3StorageBackend` (`storage.py:679`), resolving secrets via
  `_sensitive_config_value` → safebox/TK (`storage.py:171`) and non-secrets via
  `_env_backed_config_value`.

**Notation.** `<TEST_SLUG>` = an existing test-mode business used as the canary.
`<ACCT>` = Cloudflare R2 account id. Run VPS commands over
`ssh -i ~/.ssh/takyon_argon_alpha14 root@137.184.75.57` and on the subuser host
(`10.116.0.3`) as documented per CLAUDE.md. Replace placeholders before running.

---

## Step 0 — Pre-flight baseline (capture rollback truth)

**Do.** Record the current good state so you can prove "unchanged" later.

```bash
# Baseline: static + /api both work today via the VPS path.
curl -sS -o /dev/null -w '%{http_code}\n' https://<TEST_SLUG>.fourmanifold.com/
curl -sS -D- -o /dev/null https://<TEST_SLUG>.fourmanifold.com/api/takyon/apps/<TEST_SLUG>/session \
  | grep -i -E 'server:|via:|cache-control:'
# Baseline: direct non-CF hit to origin is already blocked (from a non-CF host/IP).
curl -sS -o /dev/null -w '%{http_code}\n' \
  --resolve <TEST_SLUG>.fourmanifold.com:443:137.184.75.57 \
  https://<TEST_SLUG>.fourmanifold.com/        # expect 403 (edge-only lockdown)
# Note the current live build_id (rollback / backfill reference).
ssh -i ~/.ssh/takyon_argon_alpha14 root@137.184.75.57 \
  "psql \"$DATABASE_URL\" -tAc \"select live_build_id from app_surface_contracts where business_slug='<TEST_SLUG>'\""
```

**Verify.** Static = `200`; `/api` shows live runtime headers (uvicorn/Caddy, not
a Worker); direct origin hit = `403`; `live_build_id` is a 16–64 hex string.

**Rollback.** None — read-only. (Guards: §4.1, §2.3.)

---

## Step 1 — Create the R2 bucket `product-sites`

**Do.** In Cloudflare R2, create a **new** bucket `product-sites` (separate from
Supabase `business-workspaces`). Do **not** enable an `r2.dev`/custom **public**
bucket URL — access is Worker-only.

```bash
# Cloudflare dashboard → R2 → Create bucket → name: product-sites
# (or)  npx wrangler r2 bucket create product-sites
npx wrangler r2 bucket list | grep product-sites
```

**Verify.** Bucket exists; **public access is OFF** (no `r2.dev` URL). Confirm in
the bucket's Settings → Public access = disabled.

**Rollback.** `npx wrangler r2 bucket delete product-sites` (empty bucket, nothing
references it yet). (Guards: §1.1, §1.7.)

---

## Step 2 — Mint a bucket-scoped R2 **write** token into TK (safebox)

**Do.** In Cloudflare → R2 → Manage API Tokens, create an **S3 API token** scoped
**Object Read & Write** to **`product-sites` only** (not account-wide, not other
buckets). Capture `Access Key ID`, `Secret Access Key`, and the endpoint
`https://<ACCT>.r2.cloudflarestorage.com`. Store the **secrets** in safebox/TK
(host `67.205.158.170`) under the same authority pattern as `SUPABASE_S3_*`; put
the **non-secret** endpoint/bucket in the runtime config the VPS reads.

```bash
# Secrets → safebox/TK (resolved by _sensitive_config_value on the VPS):
#   R2_S3_ACCESS_KEY_ID, R2_S3_SECRET_ACCESS_KEY
# Non-secret → env/config (resolved by _env_backed_config_value):
#   R2_S3_ENDPOINT=https://<ACCT>.r2.cloudflarestorage.com
#   R2_BUCKET=product-sites
# Verify the VPS can resolve them WITHOUT them appearing in os.environ of a business tool:
ssh -i ~/.ssh/takyon_argon_alpha14 root@137.184.75.57 \
  "cd /opt/takyon/hermes-agent-main && python -c \"from plugins.takyon import storage as s; \
   print('write_key_present', bool(s._sensitive_config_value('R2_S3_ACCESS_KEY_ID'))); \
   print('endpoint', s._env_backed_config_value('R2_S3_ENDPOINT')); \
   print('bucket', s._env_backed_config_value('R2_BUCKET'))\""
```

**Verify.** `write_key_present True`, endpoint/bucket correct. Confirm the token's
scope in Cloudflare is **`product-sites` only** (so a leak can't touch
`business-workspaces` or any provider key — §3.1). The secret must **not** appear
in `git grep`, the Caddyfile, the dist, or the Worker.

**Rollback.** Revoke the token in Cloudflare (publishing stops; serving from
already-published builds is unaffected — §3.4). Remove the TK entries. (Guards:
§3.1, §3.3, §3.4.)

---

## Step 3 — Deploy the publish-mirror (VPS → R2), but do not flip serving

**Do.** Add a publish-mirror that, on each product publish, copies **only** the
build artifact `business-workspaces:<slug>/__takyon/builds/<build_id>/<rel>` →
`product-sites:<slug>/<build_id>/<rel>`, then writes `product-sites:<slug>/current`
= the validated `live_build_id` from `app_surface_contracts` (same source as
`live_build_pointer`, `core.py:881`). Implement the R2 side as an
`R2S3StorageBackend` mirroring `SupabaseS3StorageBackend` (`storage.py:679`) —
fail-closed `StorageUnconfigured` if any cred is missing. **`current` is written
LAST, after all object PUTs succeed.** The mirror is **purely additive**: it does
not alter `_serve_product_site_file` or the Supabase write.

**Verify (dry, on one publish — still served from VPS).**

```bash
# Trigger a republish of <TEST_SLUG> through the normal publish path, then:
npx wrangler r2 object get product-sites/<TEST_SLUG>/current --pipe   # = the live build_id
# Object set under the build prefix is present:
npx wrangler r2 object get product-sites/<TEST_SLUG>/<BUILD_ID>/index.html --pipe | head -c 64
```

**Verify (no private leak into R2 — §1.1, §3.3).**

```bash
# There must be ZERO workspace/CAS/manifest/.env keys under the slug in R2:
#   (list the slug prefix; every key must be <slug>/<build_id>/... or <slug>/current)
npx wrangler r2 object list product-sites --prefix <TEST_SLUG>/ \
  | grep -E '__takyon|\.env|/cas/|/manifests/|\.key$' && echo 'LEAK — STOP' || echo 'clean'
```

**Rollback.** Disable the mirror (feature flag / revert the publish hook); R2
objects are harmless and ignored while the Worker isn't bound. Optionally
`wrangler r2 object delete` the test keys. (Guards: §1.1, §1.5, §3.1, §3.3.)

---

## Step 4 — Backfill existing live builds into R2

**Do.** For every business with a live build, run the mirror once (idempotent: it
re-PUTs the same content-addressed bytes; `current` is rewritten to the same
build_id). Prioritize active/live businesses; paused ones can backfill lazily on
next publish.

```bash
# Backfill loop (pseudo): for each slug with non-empty live_build_id, run the mirror.
# Then audit a sample:
for s in <TEST_SLUG> <slugA> <slugB>; do
  echo -n "$s current="; npx wrangler r2 object get product-sites/$s/current --pipe; echo
done
```

**Verify.** Each backfilled slug has a `current` pointer + a populated
`<slug>/<build_id>/` prefix, and the leak grep (Step 3) is `clean` for the whole
bucket:

```bash
npx wrangler r2 object list product-sites \
  | grep -E '__takyon|\.env|/cas/|/manifests/|\.key$' && echo 'LEAK — STOP' || echo 'bucket clean'
```

**Rollback.** Backfill is additive; nothing serves from R2 yet. To undo, delete
the bucket contents (`wrangler r2 object delete` per key) — VPS serving unaffected.
(Guards: §1.1, §1.4, §3.3.)

---

## Step 5 — Deploy the Worker (static-only), R2 read **binding**, NOT yet routed

**Do.** Deploy a Cloudflare Worker that:

1. **First line:** if `url.pathname` starts with `/api/` (also `/auth/`,
   `/billing/`, `/api/webhooks/`, `/api/pty`) → **pass to origin**, no cache, no R2.
   (§2.1, §2.3.)
2. Derive `<slug>` from `Host` by stripping `fourmanifold.com`; validate against
   `^[a-z0-9][a-z0-9-]{0,78}[a-z0-9]$ | ^[a-z0-9]$`; reject reserved
   (`app`/`skills`/`www`/`admin`/`dashboard`/`research-composer`) and invalid → 404.
   (§1.2, §2.4.)
3. Sanitize `<rel>` from the path: decode percent-encoding once, replace `\`→`/`,
   split on `/`, reject any empty/`.`/`..` segment; empty → `index.html`. (§1.3.)
4. Resolve `build_id` from `env.PRODUCT_SITES.get('<slug>/current')` (read-through,
   short TTL); validate `^[0-9a-f]{16,64}$`; missing/corrupt → 404/503 `no-store`.
   Client **never** supplies build_id. (§1.4, §5.5.)
5. `obj = env.PRODUCT_SITES.get('<slug>/<build_id>/<rel>')`; SPA fallback for
   unknown non-asset path → `<slug>/<build_id>/index.html` (using the **resolved**
   build_id). (§1.3, §1.6.)
6. Cache-Control: HTML `no-cache`; content-hashed assets immutable; stable static
   short TTL. No `Set-Cookie`, no permissive CORS. (§1.6, §2.2, §2.5.)
7. R2 access is the native **read binding** only — no S3 secret in Worker env; the
   Worker never `.put`/`.delete`/`.list`. (§1.7, §3.2.)

Bind R2 in `wrangler.toml`:

```toml
[[r2_buckets]]
binding = "PRODUCT_SITES"
bucket_name = "product-sites"
```

Deploy **without** a route (or to a `workers.dev` preview) first.

```bash
npx wrangler deploy            # no *.fourmanifold.com route yet
# Smoke the preview URL with a known slug (Host override):
curl -sS -H 'Host: <TEST_SLUG>.fourmanifold.com' https://<worker>.workers.dev/ -o /dev/null -w '%{http_code}\n'  # 200
curl -sS -H 'Host: <TEST_SLUG>.fourmanifold.com' "https://<worker>.workers.dev/../etc/passwd" -o /dev/null -w '%{http_code}\n'  # 404 (traversal rejected)
curl -sS -H 'Host: app.fourmanifold.com' https://<worker>.workers.dev/ -o /dev/null -w '%{http_code}\n'  # 404 (reserved)
curl -sS -H 'Host: <TEST_SLUG>.fourmanifold.com' "https://<worker>.workers.dev/api/takyon/apps/<TEST_SLUG>/session" -o /dev/null -w '%{http_code}\n'  # passes to origin, not 404-from-R2
```

**Verify.** Valid static = `200` from R2; traversal/reserved/invalid-slug = `404`;
`/api/*` is passed to origin (not served from R2). No secret in `wrangler.toml` or
the Worker bundle (`grep -RniE 'access.?key|secret|safebox|api.?key' dist/ src/` →
nothing). (Guards: §1.2–§1.7, §2.1, §2.4, §3.2, §3.3.)

**Rollback.** `npx wrangler delete` the Worker; nothing was routed, production
unaffected. (§5.1.)

---

## Step 6 — Bind the Worker to ONE test business host, verify end-to-end

**Do.** Add a Cloudflare **route** for **only** `<TEST_SLUG>.fourmanifold.com/*`
to the Worker. Ensure `/api/*` still reaches origin: either (a) a **more-specific**
route `<TEST_SLUG>.fourmanifold.com/api/*` → origin (no Worker), or (b) rely on the
Worker's first-line `/api/` passthrough (Step 5.1). Confirm the origin fetch does
**not** re-enter the Worker (use a service binding or an origin route the Worker
doesn't own — avoid the §2.6 loop). Add **no** new grey/DNS-only origin record if
the proxied product host can serve as the `/api` origin (§4.2).

**Verify — static from R2:**
```bash
curl -sS -D- https://<TEST_SLUG>.fourmanifold.com/ -o /dev/null \
  | grep -i -E 'cf-ray:|server:|cache-control:'   # cf-ray present, HTML no-cache
curl -sS https://<TEST_SLUG>.fourmanifold.com/assets/<hashed>.js -o /dev/null -w '%{http_code}\n'  # 200, immutable
```
**Verify — /api still works (unchanged runtime):**
```bash
curl -sS -D- https://<TEST_SLUG>.fourmanifold.com/api/takyon/apps/<TEST_SLUG>/session -o /dev/null \
  | grep -i -E 'server:|cache-control:'           # runtime headers, no-store/no-cache, NOT a Worker/R2 response
```
**Verify — sign-in (magic-link/session) round-trips:**
```bash
# Request a magic link / verify a session through the normal app auth endpoints and
# confirm Set-Cookie is set by the runtime and the session validates on a follow-up call.
curl -sS -i -X POST https://<TEST_SLUG>.fourmanifold.com/api/takyon/apps/<TEST_SLUG>/auth/request ...
```
**Verify — a test checkout intent:** create a checkout intent via
`/api/takyon/apps/<TEST_SLUG>/checkout` (test mode) and confirm it reaches the
runtime money gate (reserve→…), is **not** cached, and returns a real intent.
**Verify — edge loop / single hop:** `/api` response shows exactly one origin hop,
no `522`/loop.
**Verify — origin lockdown intact:**
```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  --resolve <TEST_SLUG>.fourmanifold.com:443:137.184.75.57 \
  https://<TEST_SLUG>.fourmanifold.com/          # still 403 (direct non-CF)
```

**Rollback (single action, seconds).** **Unbind/disable the Worker route** for
`<TEST_SLUG>.fourmanifold.com`. Static immediately falls back to the unchanged VPS
path; `/api` was never moved. No rebuild/DNS/VPS change. (Guards: §2.1, §2.3, §2.5,
§2.6, §4.1, §5.2, §5.3.)

---

## Step 7 — Cut over `*.fourmanifold.com` to the Worker

**Do.** Once the test host passes **all** of Step 6, bind the wildcard route
`*.fourmanifold.com/*` to the Worker (excluding reserved hosts, mirroring the
Caddyfile `@product` exclusions, and keeping `/api/*` → origin per Step 6). Reserved
hosts (`app`/`skills`/…) keep going to operator Caddy → :9119 with **no** Worker
(§2.4).

**Verify.** Spot-check several existing product hosts:
```bash
for s in <slugA> <slugB> <slugC>; do
  echo -n "$s static="; curl -sS -o /dev/null -w '%{http_code}' https://$s.fourmanifold.com/;
  echo -n " api="; curl -sS -o /dev/null -w '%{http_code}' https://$s.fourmanifold.com/api/takyon/apps/$s/session; echo
done
# Reserved host unaffected:
curl -sS -D- https://app.fourmanifold.com/ -o /dev/null | grep -i cf-ray   # served as before (operator path)
```
All static = `200` from edge; all `/api` = live runtime; `app.fourmanifold.com`
unchanged.

**Rollback.** Unbind the wildcard route (revert to the test-only route or remove
entirely) → all product static falls back to the VPS path instantly; `/api`
unaffected. (Guards: §2.4, §5.3.)

---

## Step 8 — Verify on a FRESH business (the real acceptance gate)

**Do.** Per CLAUDE.md, the **final** acceptance check is a **brand-new business
created end-to-end through the browser UI** — existing-business checks are
exploration only. Create one fresh business via `app.fourmanifold.com`, let it
publish its product, then exercise it **as a user** in the browser at
`<fresh-slug>.fourmanifold.com`.

**Verify.**
* Static loads from R2 (response has `cf-ray`, HTML `no-cache`, hashed assets
  immutable) — confirm the build was mirrored to R2 (`current` + build prefix) by
  the **bootstrap** publish path, not a manual backfill.
* Sign-in (magic link → session) works.
* A test checkout intent reaches the runtime money gate and is not cached.
* Cross-tenant probe: from the fresh host, attempt to read another slug's content
  via Host spoof / traversal / a guessed build_id → all `404`. (§1.2–§1.5.)
* Direct non-CF hit to origin for the fresh host = `403`. (§4.1.)
* `/api/*` for the fresh host reaches the runtime unchanged. (§2.3.)
* Leak grep over the fresh slug's R2 prefix is `clean` (no `__takyon/workspace`,
  `.env`, `/cas/`, `/manifests/`). (§1.1, §3.3.)

**Rollback.** Same as Step 7 (unbind route). If the fresh-business path fails,
**stop the cutover**, roll back to the VPS static path, and fix the bootstrap
publish→mirror before retrying. (Guards: all of §1–§4; CLAUDE.md fresh-business
gate.)

---

## Step 9 — Remove VPS static materialization (last, reversible)

**Do.** Only after Step 8 is clean: retire the VPS static serving path
(`_serve_product_site_file` materialization + on-VPS build dir + the subuser Caddy
`@product` static handling that fronts it), so the VPS is out of the static path.
Do this **reversibly**: first **disable** (feature flag / leave the code in place
and route static exclusively through the Worker), confirm a quiet period, **then**
delete the materialization code and any local product-site dirs in a later change.
Do **not** delete the Supabase build artifacts in the same step — they remain the
deep rollback source. Keep the subuser `/api/*` routing untouched.

**Verify.**
* All product static is served by the Worker/R2 (origin static handler no longer
  receives product GETs — check subuser access logs show only `/api/*`).
* `/api/*` still reaches the runtime for every host. (§2.3.)
* Direct non-CF origin hit still `403`. (§4.1.)
* Rollback still possible: temporarily unbinding the Worker route on a test host
  surfaces the VPS path again **iff** the materialization code is still present
  (disable-not-delete) — confirm before the later destructive delete.

**Rollback.** Re-enable the materialization path (flag back on) and unbind the
Worker route → VPS serves static again. After the destructive delete (later
change), rollback is a code revert + redeploy; the Supabase build artifacts were
never deleted, so no data loss. (Guards: §5.1, §5.4, §5.5.)

---

## Appendix — Per-step rollback summary

| Step | Forward action | Instant rollback |
|---|---|---|
| 1 | Create R2 `product-sites` | Delete empty bucket |
| 2 | R2 write token → TK (scoped to bucket) | Revoke token; remove TK entries |
| 3 | Deploy publish-mirror (additive) | Disable mirror flag; serving unaffected |
| 4 | Backfill builds into R2 | Delete R2 keys; VPS still serves |
| 5 | Deploy Worker (no route) | `wrangler delete`; nothing routed |
| 6 | Route ONE test host → Worker | **Unbind that route** (seconds) |
| 7 | Route `*.fourmanifold.com` → Worker | **Unbind wildcard** → VPS fallback |
| 8 | Fresh-business E2E acceptance | Unbind route; fix bootstrap mirror |
| 9 | Remove VPS materialization (disable→delete) | Re-enable flag / code revert; Supabase artifacts intact |

**Two things that are true at every step:** (1) `/api/*` is never moved, so any API
issue is reverted by unbinding the Worker, and (2) the VPS static path stays live
as the rollback target until Step 9 — so "roll back" is always a single
route-unbind, never a data migration.
