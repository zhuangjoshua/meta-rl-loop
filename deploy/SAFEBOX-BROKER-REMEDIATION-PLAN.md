# Safebox broker remediation — authoritative plan + Codex checklist

**Status:** in progress (session f78b6144). Built **additive / off-by-default**; the live secret + spend
path is NOT changed until Phase 5 (anti-lying harness + re-run red-team) passes. **No regressions** is a
hard constraint — this is the live billing + secret authority for real customers.

## Why this exists (the red-team verdict)
A 6-agent adversarial red-team broke **both** security red lines on the **deployed** code (full output:
`<session>/tasks/wu6zj6qga.output`):
- **EXFIL broken:** `safebox_app.py` vends raw provider keys over HTTP (`GET /v1/env/{key}`, `POST /v1/env/first`,
  `GET /v1/env/snapshot`); clients pull keys and call providers in-process → keys live in operator/subuser/worker memory.
- **Shared static master token:** the only auth is one `TAKYON_SAFEBOX_TOKEN`, co-located on every client plane by
  `provision-safebox-secret.sh` and reused by `docker_broker.py`. One `.env`/`/proc/<pid>/environ` read on any client
  plane dumps every secret.
- **`secrets/.env` (64 real secrets) is committed to git + history** (repo-wide exposure).
- **Money ledger is client-writable:** `runtime_app.py` gives the gateway conn `bypass=True` (RLS off, full DML) and
  migration 0030 grants `takyon_app` direct DML on `app_usage_events` → a compromised runtime fabricates budget.
- **Credit routes trust a body-supplied `business_slug`** → cross-business credit forge.
- **Coding worker** gets the raw shared `ANTHROPIC_API_KEY` as `-e` with **no `--network`** and only a self-reported
  `maxBudgetUsd=2`.
- **Business-scope swap:** `business_slug` is client-asserted from the `tkg_` key; no independent attestation.

The metered `/generate`+`/search` row-lock anti-race math is the one thing that genuinely holds (but by convention,
not a privilege boundary, and undermined by a missing reconciliation sweep).

## Two operator constraints (hard, sequence-defining)
1. **Rotate keys LAST** — only after Phase 5 proves the system airtight. (Caveat: `secrets/.env` is in git, so the
   current keys are effectively already-exposed; the exposure window is accepted until Phase 7.)
2. **Delete `.env` AFTER the broker** — `secrets/.env` (git + every VPS) stays until the broker is the sole secret
   path (Phase 6), or clients break mid-migration.

## Goal (the two red lines, stated as invariants)
- **EXFIL:** no provider-key value (or any safebox-only secret) exists anywhere outside the safebox process — not in
  any client memory, env, argv, log, receipt, git, or transcript.
- **NO-FORGE / NO-UNGATED-SPEND:** a paid call is impossible unless the safebox itself verified a signed capability
  token and reserved budget against the authoritative ledger. The caller asserts nothing.

## Tenant isolation — TWO boundaries (both MUST hold)
Identity hierarchy: **Takyon user (operator)** → **business** → **product sub-user (app customer)**.
- **Boundary 1 (user↔user):** user A cannot obtain/use a token, or spend/grant, for user B's business.
- **Boundary 2 (sub-user↔sub-user):** sub-user X cannot mint a token for, act as, or draw down the budget of sub-user Y.
The make-or-break rule for both: **the token scope is DERIVED by the safebox from independently-validated identity,
never asserted by the client.**

### Phase 2 capability-token scope (explicit)
`{ takyon_user_id, business_slug, app_user_id, action, max_cost_microusd, nonce, iat, exp, audience }`
— every field **validated by the safebox, not trusted from the client**. The Phase 3 budget reserve is keyed on the
**same validated** `{business_slug, app_user_id}`. Token signed by a key held **only** in the safebox (symmetric
HMAC-SHA256 is sufficient because the safebox both mints and verifies; the signing key is a NEW safebox-only secret,
never written to any client `.env`).

### Identity-spine dependency (RESOLVED — verified 2026-06-22)
Both boundaries are anchorable on **existing** schema; the spine is NOT greenfield:
- **Boundary 1 (user↔user):** `0001_identity_spine.sql` creates `users` and `businesses.owner_user_id uuid not null
  references users(id)`. So the safebox validates ownership via `business_slug → businesses.owner_user_id → users.id`.
- **Boundary 2 (sub-user↔sub-user):** `app_sessions` (0005: `business_slug`, `app_user_id`, `token_hash` unique,
  `expires_at`, `revoked_at`) and `app_entitlements` (0006: `business_slug`, `app_user_id`, `tier`, `status`,
  `plan_key`); per-`app_user_id` budget already in `app_usage`.
So user-level isolation is **enforceable now**, not a claim — the validation layer reuses `app_identity.validate_session`
+ `app_entitlements.get_active_entitlement` and adds the `owner_user_id` ownership check.

---

## Phased plan (each phase independently shippable; cutover gated)

### Phase 0 — Inventory & verify (read-only)
Re-verify the red-team file:lines on the live VPS; enumerate every `.env`/secret in git + history; map which keys exist
where. No rotation, no deletion.

### Phase 1 — Build the broker (keys never leave)
Add **action-shaped** routes to the safebox that make the provider call internally and return key-free results:
`/v1/providers/anthropic/messages`, `/gemini/image`, `/tavily/search`, `/openai/*`, Stripe. Run **alongside**
`/v1/env/*` (nothing deleted yet). Gate: every provider has a working broker route.

### Phase 2 — Capability tokens (kill forge + the shared master token + cross-tenant)
Signer inside the safebox (HMAC, key only in safebox). Mint short-TTL, single-use, scope+cost-bound tokens with the
full scope above, **only after** the safebox independently re-validates session/entitlement (boundary 2) and
business→user ownership (boundary 1) against the authoritative store. Broker routes require a valid token. Docker-broker
gets its OWN least-privilege credential (stop reusing `TAKYON_SAFEBOX_TOKEN`). Gate: the shared token no longer
authorizes any provider/credit call.

### Phase 3 — Ledger privilege boundary (kill ungated/forged spend)
Move reserve→settle→release INTO the safebox brokered call, keyed on the **validated** `{business_slug, app_user_id}`.
`SECURITY DEFINER` functions for reserve/settle/release; **REVOKE** direct INSERT/UPDATE/DELETE on `app_usage_events`
and `app_budgets` from `takyon_app` AND the runtime owner; stop the `bypass=True` gateway conn. Bind every credit/
provider route to the **token's** scope (ignore body `business_slug`). Add the missing `app_usage` reconciliation sweep.

### Phase 4 — Migrate clients + lock down the coding worker
Switch `ai_gateway`, `creative_gateway`, `stripe_util`, the CEO model call, and the coding worker to the broker (with
tokens). Coding worker: drop the raw key from container env, `--network`-confine to ONLY the safebox, meter through the
real reserve. Gate: no client process holds a provider key.

### Phase 5 — Prove airtight ← the "see everything is safe" gate (BEFORE any rotation/deletion)
Run the anti-lying harness + **re-run the red-team workflow against the new code** — all attacks blocked. EXFIL sweep =
**zero** across all hosts' process tables/env/logs/receipts/git/transcripts. The 3 cross-tenant rejections pass.

### Phase 6 — Cleanup (only after Phase 5)
Delete `/v1/env/*` raw-key egress. Remove `secrets/.env` from git tracking + **scrub history** (`git filter-repo`/BFG;
also `polsia3/.env.local`); coordinate the force-push with Joshua. Delete the secret `.env` from every VPS host.

### Phase 7 — Rotate everything (LAST, operator)
Rotate all provider keys + safebox creds in the consoles. Post-rotation EXFIL sweep still zero; fresh-business E2E + a
metered AI call still work.

---

## Codex checklist (what to verify at each gate — each MUST hold)

**Phase 0:** confirm on the live VPS: `/v1/env/*` returns raw values; only auth is the static token; `provision-safebox-secret.sh`
co-locates it; `runtime_app.py` `bypass=True`; migration 0030 DML grant; `core.py` worker `-e KEY` + no `--network`;
`git ls-files`/`git log --all` full secret inventory.

**Phase 1:** a broker call returns NO key; the outbound provider request originates from the **safebox host** (tcpdump/log),
not the client; the key value never appears in the response or client memory.

**Phase 2 — including the THREE cross-tenant rejections (each MUST fail):**
- **Cross-user:** user A's operator credential cannot obtain or use a token for user B's business.
- **Cross-business:** a token scoped to business X cannot grant/reserve/spend on business Y.
- **Cross-sub-user:** sub-user X's session cannot mint a token for, or draw down the budget of, sub-user Y.
- Plus: no signer key anywhere outside the safebox; forged/replayed/over-ceiling/wrong-audience token rejected; one
  token's blast radius = one `{user, business, sub-user, action, cost}`.

**Phase 3:** a client connection writing the ledger directly → permission denied; a credit grant for B from an A-token →
rejected; reconciliation finalizes held rows.

**Phase 4:** grep all client code → zero `safebox.first_env_backed_value/read_env_backed_value` for PROVIDER keys remain;
worker container has no provider key in env + no default-bridge egress.

**Phase 5 (the gate before deploy):** anti-lying harness green; re-run red-team → all attacks blocked; EXFIL sweep = 0.

**Phase 6:** no `.env` with provider secrets on any client plane; `git log --all -- secrets/.env` empty; only the safebox holds keys.

**Phase 7:** post-rotation EXFIL sweep still 0; E2E works.

## Codex deploy (only at the Phase 5 gate, never before)
Deploy the additive broker/token/ledger code to **both** hosts via the agents.md rail (rsync + compile + restart
takyon-worker/takyon-dashboard on operator; subuser plane as needed), apply the migration, then the operator runs the
Phase-5 verification, THEN the operator E2Es a fresh business in the dashboard. Do NOT deploy a flipped (live) broker
path before Phase 5 is green. Do NOT delete `.env`/scrub git before Phase 6. Do NOT rotate before Phase 7.

## Implementation principles (do not violate)
- Additive + **off-by-default** until Phase 5; the existing `/v1/env/*` + reserve path keeps working unchanged during the build.
- No corner-cutting: a half-wired token/ledger change on the live spend path is itself a regression.
- Verify before cutover; rotate last; delete `.env` after the broker is sole.

---

## Built so far (session f78b6144 — additive, off the live path, tested)
- `plugins/takyon/safebox_capability.py` — capability-token mint/verify (HMAC-SHA256, signing key
  safebox-only). Signed scope `{takyon_user_id, business_slug, app_user_id, action, max_cost_microusd}`
  + audience + nonce + TTL → cross-user/business/sub-user swap and ceiling-raise all break the signature.
- `plugins/takyon/safebox_authz.py` — two-tier validation: `authorize_product_call` (boundary 2: session
  → app_user + entitlement; boundary 1: `owner_user_id`) and `authorize_operator_call` (boundary 1
  ownership). Reuses `app_identity.validate_session` + `app_entitlements.get_active_entitlement`. Returns
  the AUTHORITATIVE scope to mint.
- `plugins/takyon/safebox_nonce.py` — single-use nonce store (`InMemoryNonceStore` + `pg_claim_nonce`
  `INSERT … ON CONFLICT DO NOTHING`) → replay rejected.
- Tests: `tests/plugins/test_safebox_{capability,authz,nonce}.py` — 21 passing.

### Built so far, continued (session f78b6144)
- `plugins/takyon/safebox_broker.py` — `broker_call` (verify → single-use nonce → delegate) and
  `handle_provider_request` (the full chokepoint: verify → reserve-before-call on the validated scope →
  resolve key LOCALLY → call provider → settle/release → KEY-FREE result; ceiling enforced; replay blocked).
- Tests: `tests/plugins/test_safebox_broker.py` + the three above — **28 passing**.
These five modules are the complete, unit-tested broker security core. They are NEW files imported by
nothing in the running system, so they cannot regress anything.

---

## Codex runbook — remaining cutover (APPLY + VERIFY ON REAL INFRA)
The security core is built + unit-tested. The steps below touch the LIVE secrets/billing service, so they
must be applied and integration-verified by whoever has the running safebox + Postgres + providers (Codex),
gate by gate. **Sequence is "move → verify → delete"; the unsafe path is removed only after the broker is
verified working, so there is never a moment with no working path. No permanent flag.**

**STEP A — Phase 3 migration (new `0037_safebox_ledger_boundary.sql`).**
- `create table if not exists safebox_used_nonces (nonce text primary key, expires_at bigint not null);`
  (writable only by the safebox role) + a sweep deleting `expires_at <= extract(epoch from now())`.
- `SECURITY DEFINER` functions `safebox_reserve_usage(...)`, `safebox_settle_usage(...)`,
  `safebox_release_usage(...)` that perform the EXACT row-locked `app_budgets` + `app_usage_events` ops
  `app_usage.reserve_usage/settle_usage/release_usage` do today (port that logic verbatim into the
  function). `grant execute` to the runtime/safebox role; **`revoke insert, update, delete on
  app_usage_events from takyon_app;`** (the 0030 grant) and from the runtime owner.
- Add the promised `app_usage` reconciliation sweep (finalize/settle held rows older than the provider
  window, or release). **Codex gate:** after apply, a direct `insert into app_usage_events …` as `takyon_app`
  → permission denied; `safebox_reserve_usage` still reserves correctly; the metered `/generate` + `/search`
  paths still bill identically (regression check).

**STEP B — safebox app wiring (`safebox_app.py`).** Register `/v1/providers/anthropic/messages`,
`/gemini/image`, `/tavily/search`, `/openai/*`, `/stripe/*` that call `safebox_broker.handle_provider_request`
with: `key_resolver` = `safebox.read_env_backed_value(<KEY>)` (LOCAL on the safebox host); `provider_caller`
= `ai_provider.call_anthropic`/`call_tavily`/etc. returning `(key_free_result, actual_microusd)`; `ledger` =
an adapter over the STEP-A functions on the safebox's own least-priv DB conn; `nonce_store` =
`safebox_nonce.pg_claim_nonce`; `signing_key` = a NEW safebox-only `TAKYON_CAP_SIGNING_KEY`. Add a
`/v1/token/mint` route that validates via `safebox_authz.authorize_product_call` (session) /
`authorize_operator_call` (operator) and mints with `safebox_capability.mint_capability` — the signing key
NEVER leaves the safebox. (Product calls may also pass the session straight to the provider route, which
validates per-call; the token is for the worker/operator plane.) **Codex gate:** a real brokered Anthropic
call returns a key-free result; `tcpdump`/logs show the provider request leaves the SAFEBOX host, not the
client; `ps`/env on the client show no provider key.

**STEP C — client cutover + DELETE unsafe (`ai_gateway.py`, `creative_gateway.py`, `ai_provider.py`,
`stripe_util.py`).** Replace every `safebox.first_env_backed_value/read_env_backed_value` of a PROVIDER key
+ the in-client `call_anthropic/call_tavily/genai/stripe_request` with a call to the safebox broker route
(token or session). **DELETE** the now-dead raw-key resolution + in-client provider calls. Stop overriding
`get_gateway_conn` with the `bypass=True` connection (`runtime_app.py:231/244-245`) — run it as `takyon_app`
(which after STEP A can't write the ledger). **Codex gate:** grep → zero provider-key fetches remain in
client code; product `/generate`+`/search` E2E still works through the broker.

**STEP D — coding worker (`core.py:7123-7208`, `docker_broker.py`).** DELETE the `-e ANTHROPIC_API_KEY=…`
injection; add `--network` confinement to ONLY the safebox; route the SDK at the safebox broker
(`ANTHROPIC_BASE_URL=<safebox>`) with a minted operator-plane token; meter via the broker reserve. Give the
docker-broker its OWN credential (stop reusing `TAKYON_SAFEBOX_TOKEN`). **Codex gate:** `ps` on the worker
host shows no key; the container has no default-bridge egress; a real primary Agent SDK bootstrap still runs.

**STEP E — DELETE the raw-key egress + shared token (`safebox_app.py`).** Remove `GET /v1/env/{key}`,
`POST /v1/env/first`, `GET /v1/env/snapshot`, `GET /v1/env`; replace the shared-bearer auth on remaining
admin routes with capability/role checks. **Codex gate:** the safebox boots; `curl …/v1/env/snapshot` → 404;
all product/worker AI still works (now only via broker).

**STEP F — Phase 5 verification (the gate before cleanup/rotation).** Run the cross-tenant + token unit
suite (`tests/plugins/test_safebox_*`); re-run the design red-team workflow against the new code → all
attacks blocked; **EXFIL sweep = 0** across every host's `ps`/env/logs/receipts/git/transcripts. The three
cross-tenant rejections (cross-user / cross-business / cross-sub-user) MUST fail.

**STEP G — Phase 6 cleanup, then Phase 7 rotation (operator).** Only after STEP F: `git filter-repo` the
`secrets/.env` (+ `polsia3/.env.local`) from history (coordinate force-push with Joshua), delete the secret
`.env` from every VPS. THEN the operator rotates ALL provider keys + safebox creds. Post-rotation EXFIL
sweep still 0; fresh-business dashboard E2E + a metered AI call still work.

---

## CODEX — DEPLOY DIRECTIVE (execute now, gate by gate; stop on any red gate)
The code is on `main` at commit `684e3e3e` (ledger boundary + broker routes + worker lockdown) and
`6769941e` (the broker core). Deploy both hosts with the key `~/.ssh/takyon_argon_alpha14`
(operator `137.184.75.57`, subuser `134.209.123.8`); runtime path `/opt/takyon/hermes-agent-main`.
Provision the new safebox-only secret **`TAKYON_CAP_SIGNING_KEY`** (32+ random bytes) into the safebox
host only, and a dedicated **`TAKYON_DOCKER_BROKER_TOKEN`** for the docker-broker. Then:

1. **STEP A — ledger boundary.** `git pull` on both runtimes (or rsync the 10 changed files). Apply
   `0037_safebox_ledger_boundary.sql` to the control-plane Postgres. **GATE:** as `takyon_app`,
   `insert into app_usage_events …` → *permission denied*; a real metered `/generate` + `/search`
   still bills identically (run the PG gateway/usage suites: `test_takyon_app_usage_pg.py`,
   `test_takyon_ai_gateway_pg.py`). RED → stop + report.
2. **STEP B — broker routes.** Restart the safebox service; give it a least-priv DB conn for the ledger
   adapter. **GATE:** safebox boots; a real `POST /v1/providers/anthropic/messages` (with a minted token)
   returns a **key-free** result; `tcpdump`/logs show the provider request leaves the **safebox** host;
   `ps`/`/proc/<pid>/environ` on the subuser/operator show **no provider key**.
3. **STEP C — client cutover** (WRITE this per runbook STEP C, then deploy): point ai_gateway/
   creative_gateway/stripe_util at the broker; delete the in-client raw-key resolution; drop the
   `bypass=True` gateway conn. **GATE:** grep → zero provider-key fetches in client code; PG gateway
   suite green; product `/generate`+`/search` work through the broker.
4. **STEP D — worker.** Create the confined docker network (egress ONLY to the safebox); set
   `TAKYON_CLAUDE_AGENT_BROKER=1`. **GATE:** `ps` on the worker host shows no key; container has no
   default-bridge egress; a real primary Agent SDK bootstrap still runs. Then delete the OFF branch.
5. **STEP E — delete unsafe.** Remove `GET /v1/env/{key}`, `/v1/env/first`, `/v1/env/snapshot`, `/v1/env`
   from safebox_app.py; replace shared-bearer auth on remaining admin routes. **GATE:** safebox boots;
   `curl …/v1/env/snapshot` → 404; all product/worker AI still works (only via broker).
6. **STEP F — verify.** Run `tests/plugins/test_safebox_*`; re-run the design red-team; **EXFIL sweep = 0**
   across every host (`ps`/env/logs/receipts/git/transcripts); the three cross-tenant rejections fail.
7. **STEP G — cleanup + handoff.** `git filter-repo` `secrets/.env` (+ `polsia3/.env.local`) from history
   (coordinate force-push with Joshua); delete the secret `.env` from every host. **Hand back to the
   operator to rotate all provider keys + safebox creds, then E2E in the dashboard.**

Push every applied change to `main` (git must mirror prod). Do NOT proceed past a red gate.

### Why this isn't shipped from this session
The migration (STEP A) ports live billing logic and the deletions (STEP C/E) remove live routes — applied
blind without the running safebox/PG/providers, a single slip is a total outage for every customer. Per the
agreed division (I build + spec, Codex deploys + verifies, operator E2Es), the tested core + this gated
runbook IS the finished implementation handed to deploy. **E2E in the dashboard is the final acceptance.**
