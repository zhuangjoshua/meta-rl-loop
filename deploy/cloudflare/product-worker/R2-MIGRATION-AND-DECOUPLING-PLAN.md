# R2 product-serving — retirement + /api decoupling plan

Status as of **2026-06-22**: **R2 edge serving is LIVE.** The `CLOUDFLARE_API_TOKEN` now has
`Workers Routes:Edit`, routes are bound (`*.coscale.app/*` + per-business → `takyon-product-worker`;
`origin.coscale.app/*` → **no worker**). Acceptance gates pass: `X-Takyon-Edge: r2-product-site`
present, served bytes == R2 build, `/api/*` forwarded (returns runtime `405`, no `x-takyon-edge`, no loop).

This plan has **two independent phases**, each gated and reversible. **Acceptance gate for every
removal/cutover step**: `X-Takyon-Edge` present **AND** served bytes == R2 build, verified on a
**freshly-created business through the browser** (CLAUDE.md gate) — never "R2 writes succeed".
Concurrent pushers on `main` (Joshua) → fetch-before-push; deploy tracked rails to BOTH hosts.

Source audits (two multi-agent runs, session `f78b6144`): static-retirement audit + /api-decoupling
audit. Both found **zero hard blockers**; everything below is config/dead-code removal, not re-architecture.

---

## Phase A — retire the dead VPS/subuser **static-serving** path

The worker serves R2 bytes raw, so the old VPS local-materialize-and-serve chain is now dead weight.
**Keep** the build→R2 write/storage contract and the entire `/api` dynamic plane.

| # | Step | Gate / note |
|---|---|---|
| A1 | **(done)** Enable R2 (CF token perm + routes) | ✅ Codex |
| A2 | **(done)** Bake Umami into the build (`_inject_umami_snippet` in `_publish_product_surface_path`) + backfill existing sites | ✅ commit `9577570b`. The cutover had dropped serve-time Umami; now baked + ~30 sites republished. |
| A3 | Verify late-staged assets (ad creatives) are mirrored to R2 at stage time | worker hard-404s a missing asset (no hydrate fallback). Keep `_hydrate_missing_build_asset_from_storage` until proven. |
| A4 | Drop the meta-pixel/GSC **live-dist** in-place edits; keep the build bakes + `_republish_live_dist_to_r2` | `creative_gateway.py` pixel ensure, `core.py` GSC live-inject. NOTE: `_republish_live_dist_to_r2` reads `live_root` — keep the `installed_vps` inject that feeds it, or the immediate-effect republish breaks. |
| A5 | Remove the operator→subuser **static rsync** chain | `core._sync_subuser_product_site` / `_delete_subuser_product_site` / `_subuser_remote_*` — **vestigial: nothing reads the rsynced dir** (subuser serves off R2/DB pointer). Real edge teardown = `_delete_public_edge_product_site` → `storage.delete_public_site_from_r2`. Keep the `_subuser_vps_ssh_*` helpers (they double as the runtime-deploy rail). |
| A6 | Strip the VPS local-tree tail of `_publish_product_surface_path` + orphaned helpers (`_product_live_build_root`, `_replace_symlink_atomic`, `_replace_directory_tree_atomic`, `_make_static_publish_tree_readable`) | **GREP FIRST** — the two `_replace_*` helpers are generic; if any non-publish caller exists, keep the symbol and drop only the publish call. Keep `_product_live_current_root` (build staging). |
| A7 | Retire the `*.coscale.app` static role of `web_server._serve_product_site_file` + `_materialize_product_site_from_storage` + the mount_spa product-host detour | Keep the symbol for `/site` dashboard preview + the `/api` origin + the storage-materialize seam. Human-confirm `/site`/local-dev preview before cutting. |
| A8 | **LAST** — narrow Caddy `*.coscale.app` to `/api/*` only (both hosts), keep rate-limit zones + the `/api/webhooks/stripe` handle | This single revert restores the whole fallback → why it's last. (Largely subsumed by Phase B, which removes the operator from `/api` entirely.) |

**Phase A risks:** Umami gap (done); late-asset 404 (A3); generic-helper over-removal (grep, A6);
`_product_publish_root` dual-role (also roots public-asset/containment — do NOT delete); `/site`
dashboard + local-dev preview keep the VPS path.

---

## Phase B — decouple product `/api` from the operator (worker → subuser-direct)

**Verdict: FEASIBLE, zero hard blockers.** The operator Caddy is a *verified dumb proxy* for
`*.coscale.app/api` — no path discrimination, auth, or state. Every auth/session/checkout/webhook/usage
handler runs in-process on the subuser uvicorn (`:9119`), resolves secrets from the safebox
(`10.116.0.2:8000`), and writes shared Postgres via leaf conns — **zero operator HTTP callbacks**
(grep of app_actions/app_payments/app_identity/app_entitlements/app_supabase_auth found none). The
product Stripe webhook is delivered to `app.fourmanifold.com` (operator's **own** control-plane host),
**not** `<slug>.coscale.app`, so it is out of the customer path and not a blocker.

**Outcome:** browser → CF worker → {static→R2, `/api`→**subuser's own hardened edge**}; operator only
serves `app.fourmanifold.com`. Full plane isolation — operator outage no longer kills customer apps;
product compute and control-plane compute scale/change independently.

### Change set (tracked rails)
- **`deploy/cloudflare/product-worker/worker.js`** (`fetch`): add `env.SUBUSER_ORIGIN_HOST`; for product
  hosts (`.coscale.app`, not RESERVED/ORIGIN) forward `/api/*` via `forwardToOrigin` to `SUBUSER_ORIGIN_HOST`;
  RESERVED_HOSTS keep `ORIGIN_HOST` (operator). Preserve the original Host.
- **`wrangler.toml`** `[vars]`: add `SUBUSER_ORIGIN_HOST = "subuser-origin.coscale.app"`; keep `ORIGIN_HOST`.
- **`deploy/takyon-subuser/Caddyfile`**: add a `(coscale_cloudflare_origin_tls)` snippet + a `*.coscale.app :443`
  block importing it + `fourmanifold_edge_only` (CF-IP 403 lockdown) + the two rate-limit zones **renamed
  `*_443`** (do NOT reuse the `:80` zone names → windows merge), `reverse_proxy 127.0.0.1:9119`.
- **`deploy/shared/ensure-cloudflare-origin-cert.sh`** + `deploy/takyon-subuser/apply-caddyfile.sh`:
  provision the coscale Origin cert/key to `134.209.123.8` (helper already parameterized via
  `TAKYON_CLOUDFLARE_ORIGIN_CERT_PATH/KEY_PATH` + `TAKYON_REMOTE_CLOUDFLARE_ORIGIN_CERT/KEY`).
- **`deploy/takyon-subuser/bootstrap-host.sh`**: add a `ufw` ruleset (allow 80/443 from CF ranges + 22 admin
  + VPC, default deny) — defense-in-depth before exposing `:443` directly. (ufw is currently UNMANAGED/inactive.)

### Operator / Codex actions (out-of-repo)
- **Cloudflare DNS (coscale.app):** grey-cloud (DNS-only) A record `subuser-origin.coscale.app → 134.209.123.8`,
  **NOT proxied, NOT on the worker route.** ⚠️ Because a wildcard `*.coscale.app/*` worker route is bound,
  you MUST add a more-specific `subuser-origin.coscale.app/*` route → **no worker** (same exclusion as
  `origin.coscale.app/*`), or the `/api` forward loops back into the worker.

### Ordered cutover (canary-first, operator removed LAST)
1. Provision the coscale cert/key onto the subuser. *(no Caddy/worker change yet)*. Gate: `openssl x509` ok + cert/key modulus match.
2. Add the grey-cloud `subuser-origin.coscale.app` DNS record + the no-worker route exclusion. Gate: `dig` → 134.209.123.8 (grey), route excluded.
3. Add the subuser `*.coscale.app :443` block (coscale tls + `fourmanifold_edge_only` + `*_443` rate zones + `→127.0.0.1:9119`); apply. Gate: `caddy validate` ok; from a **non-CF** host `curl --resolve <slug>:443:134.209.123.8` → **403** (edge-only holds).
4. Deploy the per-host worker change bound to **ONE canary slug** only. Gate: a **fresh business via browser** on the canary — full auth + checkout + webhook + usage E2E passes.
5. Flip ALL product hosts to `SUBUSER_ORIGIN_HOST` (operator out of `/api`). RESERVED_HOSTS stay on operator. Gate: **another fresh business** E2E + sample 2–3 existing live businesses.
6. **OPERATOR REMOVAL (last, after a soak window):** remove the operator `*.coscale.app` block (argon Caddyfile:129–139) + the `:80 @product` relay (156–165). Gate: webhook still delivers 2xx to subuser; full E2E green.

**Phase B risks:** worker-loop if the `subuser-origin` route isn't excluded (HIGH); rate-limit zone
name collision (use `*_443`); edge-only gap during cutover (block must 403 non-CF before the grey
record is live); subuser origin-IP advertised publicly (CF-IP allowlist mitigates, ufw deepens);
reserved-host branching bug → 404 the dashboard (test app.fourmanifold.com after the worker change);
**verify live, don't assert**: which routes are bound, VPC reachability subuser→safebox.

---

## What's done vs staged
- ✅ A1 (R2 enabled, Codex), A2 (Umami bake + backfill, `9577570b`), Meta pixel live.
- ⏳ A3–A8 + all of Phase B: staged. Phase B needs operator infra (DNS record + cert provisioning) and
  is canary-first; the tracked-rail code/config changes can be authored + reviewed before any production flip.
- 🚫 Nothing that pulls the fallback or touches the auth/payment path is executed without its gate + a fresh-business browser E2E.
