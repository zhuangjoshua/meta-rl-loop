# Egress / Connections Rail — hardened build spec (delta 6)

The "any integration can be gotten" capability: agent-written Deno action code calls a third
party through `ctx.egress(...)` and **never sees the credential**. Designed + adversarially
red-teamed (2 hostile-subuser passes, xhigh) 2026-07-04. **Build this spec, not a fresh
design** — the red-team caught three exploitable holes a naive build ships; every fix is
folded in below. Subusers are assumed EVIL (they author the action code). Ride existing seams
(modular.md golden rule); fail closed everywhere; do not weaken any subuser boundary.

## Architecture (verified sound)

Double-hop, exactly like `generate`: Deno action `ctx.egress({connection, method, path,
headers?, body?, query?})` → hairpins over the runner's session-Bearer fetch shim
(`app_actions.py:133-142`, rails-origin only) → new subuser rail `POST
/api/takyon/apps/{business}/egress` (SESSION_REQUIRED) → `safebox.egress_call` →
safebox `POST /v1/egress`. **The credential exists in plaintext only in the safebox process
stack frame.** The safebox mints/verifies a signed `connection.egress` capability (scope's
`business_slug`/`app_user_id` are authoritative, un-spoofable), resolves the
`provider_connections` row by the *signed* slug, validates the target, reserves the usage
ledger, unseals + attaches the credential on the single outbound request, calls upstream with
redirects OFF, settles/releases, and returns a **key-free, redacted, bounded** response.

## New state — migration `NNNN_provider_connections.sql` (assign number at ship time)

Table `public.provider_connections`, owned by `takyon_migration`, DDL style byte-for-byte on
`0062_money_shape_and_operator_approvals.sql`:
- `id uuid pk default gen_random_uuid()`, `business_slug text not null references businesses(slug) on delete cascade`,
  `connection_slug text not null`, `provider_kind text not null`,
  `allowed_host text not null` (lowercased, no scheme), `allowed_path_prefix text` (optional),
  `allowed_methods text[] not null default '{GET,POST}'`,
  `placement jsonb not null default '{}'` (`{type: header|query|basic, name}`),
  `secret_ciphertext bytea`, `secret_nonce bytea`, `secret_fingerprint text` (sha256 of plaintext, never the secret),
  `scope text not null default 'business' check (scope in ('business','per_customer'))`,
  `status text not null default 'pending' check (status in ('pending','active','revoked'))`,
  `approval_id uuid references operator_approvals(id)`, `created_at/updated_at`, `metadata_json jsonb`.
- **Keying:** `unique (business_slug, connection_slug)`; index `(business_slug, status)`.
  Resolution is ALWAYS `WHERE business_slug = <signed scope.business_slug> AND connection_slug
  = <arg> AND status='active'` — cross-tenant is impossible (slug from the signed capability).
- **Privileges (mirror 0062):** `revoke all from public`; **`revoke all from
  takyon_app_runtime`** (subuser plane has ZERO access); `grant select,insert,update,delete to
  takyon_migration, takyon_safebox_authority`; **column-level** grants of the METADATA columns
  only to `takyon_operator_runtime`/`takyon_runtime` (create/list/revoke) — `secret_ciphertext`
  / `secret_nonce` granted **only** to `takyon_safebox_authority`. RLS enabled, policyless,
  relying on the 0060 trusted-default. No SECURITY DEFINER fns needed (column grants enforce
  the ciphertext wall; safebox reads directly via `_safebox_db_conn`).
- **Seal key** `TAKYON_CONNECTION_SEAL_KEY` is a safebox-process secret added to
  `core._SAFEBOX_SELF_AUTHORITY_SECRETS` (categorically non-egress over `/v1/env`, same class
  as `TAKYON_CAP_SIGNING_KEY`) — NOT in the migration.

## `POST /v1/egress` (safebox) — reserve → attach → call → settle

Register in `register_provider_proxy_routes` beside Anthropic/Tavily. **`_require_internal_token`
GATE (the neighboring capability-only proxy routes omit it — add it here) + signed capability
as the sole spend authority.** Body `{business, connection_slug, method, path, query?, headers?,
body?, estimate_microusd, session_token}`.

1. **Auth:** `_mint_capability_token(business, action='connection.egress', session_token=...,
   audience='connection.egress', ...)` → `authorize_product_call` derives the REAL `app_user_id`
   + entitlement. Add `'connection.egress'` to `_ACTION_AUDIENCE_DEFAULTS`. **The subuser rail
   handler ALWAYS derives scope from the validated session (app_user_id present) — the caller
   can never select a service/operator principal or the `app_user_id=None` no-cap path, and the
   bare `TAKYON_SAFEBOX_TOKEN` is NEVER egress spend authority.** (Business-shared egress from a
   scheduled/service principal is a LATER sub-delta: it must present an `authorize_operator_call`
   capability bound to the business owner — not built in v1.)
2. **Resolve** the row by signed `scope.business_slug` + `connection_slug`, `status='active'`.
   **Fail closed on `scope='per_customer'`** (no per-app-user vault exists in v1 — one row =
   one shared secret; enabling it would share a credential across all customers). 404
   `connection_unknown` if absent.
3. **§3 refusal gate:** run the brief-time capability deny (bot_evading_scrape /
   inauthentic_engagement) against the connection's host/purpose at BOTH creation and call;
   record provenance on any gate-opened capture connection.
4. **Provider-host denylist:** refuse any `allowed_host` fronting a usage_pricing-metered
   provider (OpenAI/Anthropic/Gemini/FAL/Tavily — analogous to `core.provider_key_denylist()`)
   at creation AND call. Those go through the dedicated priced brokers, never flat-fee egress.
5. **Safe URL assembly (NEVER concatenate):** base = `'https://' + allowed_host`; require
   `path` to start with `/`, reject `@`, leading `//`, backslashes, and CR/LF/NUL/control chars
   in path + query keys/values; then **re-parse the final URL** with `urlsplit` and assert
   `scheme=='https' AND hostname==allowed_host AND username is None AND password is None AND
   (port is None or the connection's port)` BEFORE reserve/attach/call. Enforce `method in
   allowed_methods` and `path.startswith(allowed_path_prefix)` when set.
6. **SSRF + real transport pin (the linchpin):** resolve the host EXACTLY ONCE; reject if ANY
   A/AAAA is internal (mirror `app_actions._is_internal_host`: loopback/link-local/RFC1918/
   RFC4193/reserved/multicast/`0.0.0.0`/`.internal`/`.local`/`169.254.169.254`; normalize
   `.ipv4_mapped` explicitly so it isn't Python-version-dependent). **Then PIN httpx to that
   concrete public IP** via a custom transport/resolver — connect-by-IP with TLS
   `server_hostname=allowed_host` and verification ON — do NOT hand the bare hostname back to
   httpx (it would re-resolve → DNS-rebind TOCTOU). **Platform-self denylist** layered on top:
   reject 137.184.75.57, 134.209.123.8, the safebox host/IP, app.fourmanifold.com, *.coscale.app
   (`_is_internal_host` does NOT block the platform's own public IPs).
7. **Metering:** price from `agent/usage_pricing.py` `request_cost` via a new
   `ai_provider.egress_request_microusd()` (mirror `tavily_request_microusd`); unpriced → 503
   `egress_pricing_unavailable` BEFORE any work. **Add a byte-based cost component** and cap
   request body (256 KiB) + response body (1 MiB) so a flat fee can't subsidize unbounded
   upstream cost. `_UsageLedgerAdapter(provider='egress')`, `reserve(scope,
   max(server_price, client_estimate))` — the ONE gate; out-of-funds/entitlement → 402 before
   any upstream call. **Concretely call `rate_limit.check_rate_limit`** keyed `{business,
   connection_slug, principal}` inside the route (not "reuse the rail").
8. **Attach:** unseal (AEAD) into the stack frame only; forward request headers via a strict
   **allowlist** (not a denylist) — drop the placement-name header, Authorization, Host,
   hop-by-hop, Cookie, Proxy-Authorization, X-Forwarded-*. For `query` placement strip any
   caller query key matching `placement.name`; for `basic` strip caller Authorization + URL
   userinfo; forbid credentials in caller path/query. Force the connection credential last and
   canonical. (Prefer forbidding `query`/`basic` placement for secrets — always reflectable.)
9. **Call:** `httpx` `follow_redirects=False`; **refuse any 3xx** → clean 502 (a redirect could
   carry the credential to an unvetted host).
10. **Return:** success → `settle` (per-request actual==price); any failure → `release` +
    `_sanitize_upstream_error` (truncate body). **Redact** the exact secret (raw + url-encoded +
    basic-auth base64) AND `secret_fingerprint` from the body and every returned header;
    response headers via a strict **allowlist** (content-type/length + a few safe). Never log
    the outbound URL/headers carrying the secret. Return only `{status, headers, body}` —
    key-free.

## `ctx.egress` wiring

Add `egress` as a client method on the scaffold `runtime-client.js` (the shared client the
browser + Deno action both get): a thin POST to `${runtimeApiBase}/egress` with no auth logic —
the runner fetch shim auto-injects the business-scoped Bearer for rails-origin URLs only.
Register via `core._RAIL_CLIENT_METHODS['egress']=('egress',)` (source-scanner regex DERIVES —
no scanner edit). Add `'egress'` to `_ACTION_RUNTIME_RAILS`. **`--allow-net` needs NO new
entry** — the action never contacts the third party; the safebox does. Add a new `egress`
`RuntimeRail` to `RUNTIME_RAILS` (`RailRoute('POST',('egress',),'egress_post',
APP_AUTH_SESSION_REQUIRED)`). Do NOT touch the social `connections` rail.

## `business_request_credential` (consent tool — the WHETHER gate, never the secret carrier)

CEO-discoverable tool (core.py + plugin.yaml). Args `{business, connection_slug, provider_kind,
allowed_host, allowed_path_prefix?, allowed_methods?, placement, scope}`. It (1) INSERTs a
`pending` `provider_connections` row (metadata only, secret columns NULL — operator/runtime has
column grants for exactly these); (2) mints an `operator_approvals` row (existing rail, 0062)
`action_kind='provider_connection_grant'`, payload_digest over the metadata, idempotent +
single-consume + TTL; (3) returns the pending approval id, slug, and out-of-band deposit
instructions — **never accepts/prints/logs the secret** (it never enters the agent transcript).
The human approves via the existing `handle_business_decide_operator_approval` AND out-of-band
POSTs the plaintext to a NEW safebox route `POST /v1/connections/deposit` (`_require_internal_token`
+ `_require_operator_client`) which verifies a matching approved row, AEAD-seals, writes
ciphertext (`takyon_safebox_authority` only), flips `status='active'`. The secret lands sealed
having touched neither the CEO model context nor the business runtime.

## Ordered build (each verified before the next)

1. Migration (table + column grants + policyless RLS) — pure DDL, verify on throwaway PG that
   subuser role gets permission-denied and operator role cannot read the ciphertext columns.
2. `usage_pricing.py` egress `request_cost` + byte component; `ai_provider.egress_request_microusd()`.
3. safebox: `TAKYON_CONNECTION_SEAL_KEY` in `_SAFEBOX_SELF_AUTHORITY_SECRETS`; AEAD seal/unseal;
   `POST /v1/connections/deposit`.
4. safebox: `POST /v1/egress` with EVERY must-fix above (URL re-parse, real IP pin,
   redaction, allowlist headers, §3 gate, provider denylist, rate limit, body caps, internal
   token). Unit-test each guard against the red-team's concrete attacks on a throwaway PG +
   a local echo/redirect/rebind server.
5. `safebox.py` `egress_call()` (mirror `broker_provider_call`, fail-closed).
6. core.py `egress` RuntimeRail + `_RAIL_CLIENT_METHODS` + `_ACTION_RUNTIME_RAILS`.
7. web_server.py `_APP_POST_HANDLERS['egress_post']` (SESSION_REQUIRED; always session-derived
   scope; map 402/403/404/502/503).
8. scaffold `runtime-client.js` `ctx.egress`; re-vendor.
9. `business_request_credential` tool + `operator_approvals` consumer.
10. Deploy operator + BOTH subuser replicas; `takyon migrate` on operator first; restart; fresh
    business E2E via CLI: CEO requests credential → operator approves + deposits → generated
    action `ctx.egress` hits a third party keyless + metered → exhaust per-user cap → clean 402
    → confirm no credential in any response/log.

## Red-team must-fix checklist (ALL must land before hostile-subuser exposure)

1. **Real httpx transport IP-pin** (connect-by-IP, TLS server_hostname=allowed_host, verify on) — NOT a `--allow-net` string mirror.
2. **Safe URL assembly + final re-parse** asserting hostname/scheme/no-userinfo — NOT the 3-char Stripe check.
3. **Response redaction** (secret raw/url-enc/base64 + fingerprint) + strict header **allowlist**; forbid query/basic secret placement.
4. **CR/LF/NUL/control-char rejection** in path/query/headers, fail-closed 400.
5. **Header forwarding via allowlist**, always strip placement-name + Authorization + Host + hop-by-hop + Cookie + Proxy-Authorization + X-Forwarded-*.
6. **`_require_internal_token`** on `/v1/egress` and `/v1/connections/deposit`; signed capability is the sole spend authority; single-use nonce.
7. **Platform-self egress denylist** (both droplet IPs + safebox + app.fourmanifold.com + *.coscale.app).
8. **`.ipv4_mapped` normalized** so the internal-IP guard isn't Python-version-dependent.
9. **Provider-host denylist** (usage_pricing-metered providers) + byte-cost basis + request/response body caps — no flat-fee metering bypass.
10. **Session-derived scope only** in the subuser rail; no caller-selectable service/operator/`app_user_id=None` path; bare safebox token never spend authority.
11. **Fail closed on `scope='per_customer'`** in v1 (creation + resolver) until the per-app-user vault exists.
12. **Wire §3 refusal** (bot_evading_scrape / inauthentic_engagement) into creation AND the egress call; provenance on gate-opened capture.
13. **Resolve host once**, validate that single result, pin — no two-independent-resolution TOCTOU.
14. **Concrete `rate_limit.check_rate_limit`** in the route keyed `{business, connection_slug, principal}`; cap forwarded request body.
15. **Generalize header/placement drop to all placement types** (query key, basic userinfo); force connection credential canonically.

## Deferred sub-deltas (NOT v1)

Per-customer OAuth vault + token refresh/rotation/revocation (unlocks `scope='per_customer'`);
operator/CEO-plane egress branch (`_OperatorBudgetAdapter`); richer per-connection enumerated
path allowlist; idempotency-key passthrough for side-effecting POST egress.
