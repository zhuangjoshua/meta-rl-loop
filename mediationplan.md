# Takyon Control Plane — Supabase Plan

Working plan for the next Takyon backend rails. Target source of truth: **Supabase Postgres** (+ Supabase Storage for the filesystem). This replaces the SQLite-first framing.

## Ground Truth (verified by inspection, 2026-05-30)

- The canonical trunk is `takyon/` → `hermes-agent-main` (`takyon-agent 0.14.0`). Its control/business state today is **local SQLite** at `.takyon/state.sqlite3` (`core.py` connects to it; every E2E run dir holds a `state.sqlite3`).
- **The target Supabase is NOT greenfield.** `DATABASE_URL`/`MIGRATION_DATABASE_URL` in `secrets/.env` point at a project that still hosts the **populated `polsia2` ("Argon Clone") control schema** — ~143 user tables, identity centered on `public.profiles` (there is no `users` table), with **live data**: `public.businesses` (~32 rows; `owner_profile_id`, `slug`, `status`, `mode`, `last/next_ceo_wake_at`), `public.billing_accounts` (~15 rows; Stripe-subscription shape — `business_id`, `stripe_customer_id`, `stripe_subscription_id`, `subscription_status`, `current_period_end`), `agent_runs` (~70 rows), plus `business_memberships`, `profile_ai_wallets`, `project_ai_proxy_keys`, `creator_payout_accounts`, `generated_app_users`, `idempotency_keys`. There is **no `takyon` schema**. polsia2's *code* is discarded; its *schema + data on this database are live* and must be treated as a live dependency, not "prior art."
- **Collisions are real, not hypothetical.** The plan's `businesses` and `billing_accounts` names ALREADY EXIST and are populated, under a different shape (profile-based; Stripe-subscription based) — a greenfield `create table public.<name>` would silently shadow them. Confirmed FREE (no `public`/`takyon` object) by the same survey: `users`, `user_api_keys`, `billing_entries`, `custody_accounts`, `custody_entries`. The top-level identity spine (`users`, `user_api_keys`, `owner_user_id`) genuinely does not exist yet.
- **There IS data to reconcile** (`businesses`, `billing_accounts`, `profiles`): the earlier "no real data to migrate / trivial backfill" framing was **wrong and is retracted**. Choosing replace-vs-extend-vs-isolate for each overlapping table is the gating work for Phases 1–2 and MUST precede any DDL (see **Build Discipline** below). Never apply migrations to this shared, populated database without explicit operator confirmation.
- **Decision (2026-05-30, operator): REPLACE polsia2's `public` control tables.** Takyon owns `public`. The Phase 1–2 migrations deliberately **drop/replace** polsia2's overlapping objects (`businesses`, `billing_accounts`, and whatever FK-depends on them) in dependency order, then install the takyon shape; the FREE names (`users`, `user_api_keys`, `billing_entries`, `custody_accounts`, `custody_entries`) create cleanly. polsia2's live rows are treated as **disposable**. **Guardrail (robustness #1):** design and verify the replace on **LOCAL Postgres first**; the live Supabase apply is a *separate* step requiring (a) a fresh Supabase backup/snapshot and (b) an explicit operator go-ahead — `drop` is irreversible. Migrations stay idempotent/re-runnable.
- **Cutover mechanics (built + locally verified, 2026-05-30).** The REPLACE is implemented as two pieces, not a blind drop: **(1) fail-loud REPLACE guards** at the top of `0001_identity_spine.sql` and `0002_ledgers.sql` — if a non-takyon `businesses`/`billing_accounts` is present (detected by absence of `owner_user_id` / `allowance_included_cents`) the migration RAISES and names the teardown, instead of `create table if not exists` silently binding to polsia2's incompatible shape; **(2) a separate gated teardown** `plugins/takyon/db/retire_polsia2_public.sql` that lives OUTSIDE `db/migrations/` (so the test sweep and any forward-migrate never run it), idempotently drops `billing_accounts` then `businesses` with `cascade` (clearing dependents' FK constraints, not their tables), and — crucially — **refuses to drop a table takyon already owns**, so a re-run can never nuke takyon data. **Live cutover order** (only after backup + explicit go-ahead): `retire_polsia2_public.sql` → `0001` → `0002` → …. Verified on local PG by `tests/plugins/test_takyon_retire_polsia2_pg.py` (guard fires on a simulated polsia2 shadow; retire→migrate yields the takyon shape with resolved FKs; teardown is a no-op on a clean DB; re-run preserves takyon rows). **Still gated/outstanding:** full retirement of the *rest* of polsia2's `public` (profiles, agent_runs, the now-orphaned `business_id` dependents) needs a verified live table inventory + a Supabase role/grant review — that needs a live `DATABASE_URL` read, which the operator has not yet authorized; not a new credential, an authorization to connect.
- Supabase project already provisioned (`DATABASE_URL` in `secrets/.env`); the runtime currently only passes it *through* to generated apps.

## Target Architecture

```
Takyon user ──(one opaque API key)──► Control API ─┐
Sub-user ─────(magic-link session)──► App Runtime ─┤
Generated app ─(project gateway key)► AI Gateway ──┤
                                                    ▼
                                        Internal Resolver  (the opaque control plane)
                                  identity → ownership → policy → wallet → shared provider keys → worker routing
                                                    ▼
                              Supabase Postgres (SOURCE OF TRUTH)  +  Supabase Storage (artifacts/filesystem)
                                                    ▼
                              Job queue (FOR UPDATE SKIP LOCKED) → workers (local now, Modal for heavy later)
```

Postgres is the memory. The queue is the control plane. The runtime is a **stateless, replaceable worker** — not the source of truth.

## Robustness Contract (non-negotiable invariants)

1. **All money is integer minor units (cents, `bigint`).** Never floats.
2. **Two ledgers, never merged:** (A) user→platform billing, (B) sub-user→user custody.
3. **Included allowance is NOT money** (capped, never shown as dollars). **Top-ups ARE exact money** (debited exactly, shown as money).
4. **Every state-changing operation is idempotent** (unique idempotency key); replays produce one effect.
5. **One opaque API key per user** is the entire boundary. Platform mints + rotates; user never generates it; user cannot introspect internals.
6. **Almost nothing is per-user.** Shared platform keys for inference, ONE shared X/Meta posting account, ONE shared platform Stripe for collecting sub-user payments. The only per-user external linkage is **optional, deferred Stripe Connect payout**.
7. **Zero-friction onboarding.** A user operates immediately with zero account connections; never gate onboarding on connecting anything.
8. **No silent fallback, no degraded "completed."** Work is `completed` only when it truly happened; otherwise `blocked` (missing config/budget/credential/approval) or `failed` (code/vendor error) — with a reason.
9. **Every external side effect writes a receipt** (vendor, request id, status, object id, raw error).
10. **Generated apps never hold provider keys or DB credentials** — only a project-scoped gateway key.
11. **RLS on every user-scoped table** as defense-in-depth; the runtime is the single trusted writer (service-role).

## The Supabase Schema

Extensions: `pgcrypto` (`gen_random_uuid()`), `citext` (case-insensitive email). IDs are `uuid`. Timestamps `timestamptz default now()`.

### Identity & ownership
- `users(id, auth0_sub citext unique, email citext, status, created_at, stripe_connect_account_id null, stripe_connect_status default 'none', payout_currency default 'usd')` — `auth0_sub` is the stable join key; `email` is a mutable attribute.
- `user_api_keys(id, user_id→users, key_hash bytea unique, prefix text, created_at, revoked_at null, last_used_at null)`
  - **`unique(user_id) where revoked_at is null`** → exactly one active key per user.
  - Verify: hash the presented secret, look up by hash, require `revoked_at IS NULL`, constant-time.
- `businesses` (port existing; slug stays the runtime handle) **+ `owner_user_id→users NOT NULL`**.

### Billing ledger — flow A (user → platform)
- `billing_accounts(user_id pk→users, allowance_included_cents, allowance_used_cents, allowance_period_start, allowance_resets_at, topup_balance_cents default 0)`
- `billing_entries(id, user_id→users, business_slug null, bucket enum('allowance','topup'), kind enum('grant','reserve','settle','refund','topup','debit'), amount_cents, balance_after_cents, idempotency_key unique, job_id null, created_at, metadata jsonb)` — append-only.
  - Reserved = `Σreserve − Σsettle − Σrefund`. Spend **allowance bucket first, then topup**.

### Custody ledger — flow B (sub-users → user, held by platform)
- `custody_accounts(user_id pk→users, owed_balance_cents default 0, paid_out_cents default 0, currency default 'usd')`
- `custody_entries(id, user_id→users, business_slug, kind enum('accrual','app_fee','payout','refund','adjustment'), gross_cents, fee_cents, net_cents, stripe_ref, idempotency_key unique, created_at)`
  - On sub-user payment: `fee = gross * STRIPE_CONNECT_APPLICATION_FEE_BPS/10000`; accrue `net = gross − fee` to `owed_balance`. **Accrued from day one, independent of whether Connect exists.** Payout drains `owed_balance`. Never netted against billing.

### Providers, policy, gateway
- `provider_accounts(id, kind enum('anthropic','openai','fal','tavily','x','meta','stripe_platform'), scope enum('platform'), encrypted_blob bytea null, metadata jsonb)` — all platform-scoped, encrypted with `APP_ENCRYPTION_KEY`. No per-user posting/inference creds.
- `app_execution_policies(business_slug pk, preferred_model_tier, max_runtime_seconds, max_output_bytes, allow_worker_escalation bool, allow_expensive_branches bool, quality_mode, retry_depth, monthly_app_budget_cents)`
- `app_gateway_keys(id, business_slug→businesses, key_hash unique, prefix, revoked_at null, created_at)` — what generated apps receive.

### Jobs, deployments, sub-user tier, idempotency
- `jobs(id, business_slug→businesses, kind, status enum('queued','running','completed','blocked','failed','cancelled'), idempotency_key unique, payload jsonb, result jsonb null, error jsonb null, reserved_billing_entry_id null, attempts int default 0, max_attempts, locked_by null, locked_at null, created_at, updated_at)`
- `wake_schedules(business_slug pk→businesses, kind default 'ceo_wake', enabled bool default true, interval_seconds int not null, next_run_at timestamptz not null, last_enqueued_at null, payload jsonb, created_at, updated_at)` — **the schedule lives here, not in `.takyon/cron/jobs.json`.** Replaces the local file-based cron entirely; the file ticker (`gateway/run.py::_start_cron_ticker`, `cron/scheduler.py`) is retired with the SQLite path (Phase 8). One row per recurring CEO wake; `next_run_at` is advanced by the dispatcher, never by a local process.
- `app_deployments(id, business_slug→businesses, artifact_ref (Storage path), version, url null, status, health_checked_at null)` — `url` set only after a real health check passes.
- Port the sub-user tier as-is, scoped per `business_slug`: `app_users, app_sessions, app_magic_links, app_entitlements, app_plan_policies, app_budgets`.
- `idempotency_keys(key pk, scope, request_hash, response jsonb, created_at)` — API-level idempotency (checkout, top-up, job submit, webhooks).

## Authentication Boundaries

Every entry point resolves to exactly one identity row before any privileged work. Two human/credential tiers, kept hard-separate (per CLAUDE.md: never conflate Takyon users with sub-users).

**Takyon user (top tier) — two credentials, one `users` row:**
- **Human login = Auth0** (already wired in the canonical runtime: `takyon_cli/web_server.py`, `AUTH0_DOMAIN=login.fourmanifold.com`, audience `https://argon.alpha/api`). The runtime validates the Auth0 token, reads the stable OIDC `sub`, and maps it to `users.auth0_sub`. The system already models the operator as an Auth0 subject — the existing `ARGON_LOCAL_AUTH_SUBJECT/EMAIL` bypass is the local-dev stand-in (must be **off in production**). So this formalizes an existing subject, not a net-new IdP.
- **First login = JIT provisioning** (this IS zero-friction onboarding): no `users` row for that `sub` → create `users` + `billing_account` + `custody_account` + mint the first `user_api_key`, in one transaction, with zero account connections required.
- **Programmatic calls = the opaque API key** (`tk_…`), resolved via `user_api_keys.key_hash`. Minted/rotated **only by an Auth0-authenticated dashboard session** — the user never self-serves key creation, and an anonymous caller can never mint one.

**Sub-user (product tier) — already built, reuse as-is:** magic link → hashed session (`app_magic_links` → `app_sessions`; tokens stored as `token_hash` with `revoked_at`/`expires_at`), scoped per `business_slug` → `app_users`. A sub-user is **never** an Auth0 identity and never touches the top tier.

**Machine / service boundaries:**
- Generated app → AI Gateway: **project gateway key** (`app_gateway_keys`), never provider/DB creds.
- Runtime → Postgres: Supabase **service-role** / `DATABASE_URL` — the single trusted writer (bypasses RLS). Never held in a browser.
- Stripe webhooks: **signature** (`STRIPE_WEBHOOK_SECRET`), idempotent on event id.
- Cron dispatch: **`CRON_SECRET`** bearer.
- Shared provider OAuth (X/Meta): platform-level `X_CLIENT_SECRET` / `META_APP_SECRET` → shared accounts, never per-user.

## The API Plan

### Control API — the opaque Takyon-user boundary
Auth: `Authorization: Bearer tk_<prefix>_<secret>` → hash secret → resolve `user_api_keys` → require active → resolve user. Rate-limited **per `user_id`** (Postgres fixed-window now; Upstash Redis at scale). Errors never leak internals (`402 insufficient_balance`, `429 rate_limited`, `403 not_owner`).
- `GET /v1/me` → user, plan, **topup balance as money**, allowance as opaque "included usage remaining" (never dollars).
- `GET /v1/businesses` / `GET /v1/businesses/{slug}` → owned businesses + read-only policy/usage view.
- `GET /v1/usage` → metered usage per business + account.
- `POST /v1/topups` → Stripe Checkout for an exact top-up; webhook credits `topup_balance` idempotently.
- `GET /v1/payout` → custody `owed_balance`. `POST /v1/payout/connect` → optional Connect onboarding. `POST /v1/payout/withdraw` → payout from custody (only if connected).
- **Key rotation is NOT on this API** (the user doesn't own the key). The dashboard/operator surface (Auth0 session) mints a new key + revokes the old **atomically**.
- Opaque by construction: provider keys, control-plane internals, workers, other tenants, raw allowance cost — none are reachable.

### App Runtime API — the sub-user boundary (per business, shared backend)
Owned by `takyon-app-runtime`. Sub-users hit this first (Vercel). Magic-link → session.
- `/auth, /session, /account, /usage, /checkout, /generate, /jobs/submit, /jobs/status`.
- Enforces entitlements + plan policy, records usage against the business `app_budget`, escalates heavy work to `jobs`.
- Sub-user payments collected on the **shared platform Stripe** → accrue to the **owner's custody ledger** minus app fee. Sub-users never see Takyon internals or the owner's account.

### Internal AI Gateway
- `/internal/ai-gateway/messages` — generated apps + app runtime call with the **project gateway key**. Gateway resolves business → policy → **reserves billing** → calls the shared provider key → **settles**. Generated apps never hold provider keys.

## Billing Engine (reserve-then-settle)

Before expensive work: `reserve(estimate)` on the billing account (allowance bucket first, then topup) inside one transaction with a unique idempotency key. Run job. On success: `settle(actual)` + release `(reserved−actual)`. On failure: `refund(reserved)` → state `blocked`/`failed`. If neither bucket covers the estimate → block with exact reason (`402`). A **reconciliation job** continuously asserts `Σentries == cached balances`, `reserved ≥ 0`, and alerts on drift.

## Worker Plane

`jobs` queue with `SELECT … FOR UPDATE SKIP LOCKED` pickup → one job, one worker. Idempotent execution (idempotency key), at-least-once. Result persisted atomically; partial = `blocked`/`failed`, never `completed`. Retries re-check budget (exhausted → `blocked`, not infinite retry). Local worker now; **Modal for heavy/build jobs later** under the same job contract. Every side effect writes a receipt.

### Scheduled CEO wakes (cron) — Postgres-native, no local ticker

Wakes are not a separate mechanism: they are due-rows enqueued into the same `jobs` queue. Three pieces, all in the source of truth — no systemd timer, no `jobs.json`, no `.tick.lock`:

1. **Schedule** — rows in `wake_schedules` (above). The table is the truth; nothing watches a local file.
2. **Dispatch (enqueue-when-due)** — runs every minute, gated by `CRON_SECRET` (Auth Boundaries). Preferred home: Supabase **`pg_cron`** calling an in-DB function, so there is no external process to keep alive; equivalently a single `CRON_SECRET`-bearer endpoint hit on an interval runs the same SQL. Enqueue is **exactly-once** via the `jobs.idempotency_key` unique constraint keyed on the fired window:

   ```sql
   -- dispatch_due_wakes(): enqueue, then advance, atomically
   with due as (
     select business_slug, kind, payload, next_run_at, interval_seconds
     from wake_schedules
     where enabled and next_run_at <= now()
     for update skip locked
   ),
   enq as (
     insert into jobs (business_slug, kind, idempotency_key, payload, status)
     select business_slug, kind,
            'wake:'||business_slug||':'||to_char(next_run_at,'YYYYMMDDHH24MI'),
            coalesce(payload,'{}'::jsonb), 'queued'
     from due
     on conflict (idempotency_key) do nothing      -- replays/overlap → one job
     returning business_slug
   )
   update wake_schedules w
   set next_run_at = greatest(now(), w.next_run_at) + (w.interval_seconds || ' seconds')::interval,
       last_enqueued_at = now(), updated_at = now()
   from due where w.business_slug = due.business_slug;
   ```

   Catch-up is bounded by `greatest(now(), next_run_at)` (a host that was down does not fire N backlogged wakes — one enqueue, schedule realigned to now). A missed minute self-heals on the next dispatch because `next_run_at <= now()` stays true until enqueued.
3. **Drain** — the same worker pulls `kind='ceo_wake'` jobs via `FOR UPDATE SKIP LOCKED`, runs the CEO wake turn, persists `result` atomically (`completed` only if it truly ran, else `blocked`/`failed` with a reason), writes a receipt, re-checks budget on retry. The 3-minute hard interrupt and per-business scope isolation from the old cron path move onto the worker as job-execution invariants.

Why this replaces the old ticker cleanly: in SQLite, an external loop was required because nothing watched the file; in Postgres the schedule, queue, idempotency, locking, **and** dispatch (pg_cron) all live in the source of truth, so the runtime stays a stateless, replaceable worker. (Temporal/Inngest remain an optional later upgrade for durable wake loops, not a Phase-6 requirement.)

## Runtime Cutover (SQLite → Postgres), always-working increments

1. Add a DB access layer in the runtime behind the interface `core.py` already uses; implement a Postgres backend (psycopg) reading the existing `DATABASE_URL`.
2. Add `migrations/` (numbered `.sql`) + a small idempotent runner. Port the existing schema + new identity/ledger tables, with constraints + RLS.
3. Seed: create one admin `user`; set `owner_user_id` on `filesystemsmoke*` fixtures.
4. Externalize the per-business filesystem to **Supabase Storage** (sync-down → run → sync-up); local disk = scratch.
5. **Flip** the runtime to Postgres; run full E2E through the real shell; verify identical behavior; then **delete the SQLite path** (no long-term dual backend).
6. **No-fleet proof:** a second runtime with an empty local disk resumes a business purely from Postgres + Storage.
7. Demote the VPS to one stateless worker; once the no-fleet proof passes, the VPS is disposable (scale to N / serverless).

## Security

API keys hashed (SHA-256) + prefix, constant-time compare, one-active-per-user, atomic rotation. Provider creds shared + encrypted (`APP_ENCRYPTION_KEY`), never sent downstream. Generated apps get only a project gateway key. Service-role key lives only in the trusted runtime; RLS on all user-scoped tables. Auth0 token validated server-side (verify issuer/audience/signature), keyed on the stable `sub`; **`ARGON_LOCAL_AUTH_BYPASS` must be off in production**. Stripe webhooks signature-verified + idempotent on event id. Secrets never logged.

## Test & Robustness Strategy (every part has a gate)

- **Real Postgres in tests** (ephemeral/Docker PG), never mocks — billing correctness must hit the real engine.
- **Ledger concurrency/property tests:** parallel reserves/settles never oversell; balances reconcile; idempotency prevents double-charge; reserved never negative.
- **API tests:** opaque-key auth (valid/revoked/rotated), one-active-key invariant, per-user rate limit, no internal leakage, cross-tenant isolation (user A ≠ user B).
- **Idempotency tests:** replayed top-up/checkout/job-submit/webhook → one effect.
- **Failure injection:** provider error → reserve refunded + `blocked`; worker crash mid-job → safe retry; partial artifact → `blocked`, no fake deploy URL.
- **No-fleet stateless-resume proof** (second empty-disk runtime).
- **E2E through the real shell** (`/create`, `/status`, `/pulse`, `/files`, `/read`, `/cron tick`), test businesses in test mode.

## Providers & Infra

- **Now:** Supabase Postgres (source of truth) + Supabase Storage (filesystem/object home — S3-compatible, so **no separate S3/"F3" needed**). Already provisioned.
- **Stripe:** keys present; Connect deferred/optional per the money model.
- **Later:** Modal (heavy jobs/builds). Optional Temporal/Inngest (durable cron-wake loops).
- **VPS:** keep now (demoted to a worker), retire after the no-fleet proof. No other new provider required.

## Build Discipline (applies to every phase below)

Two non-negotiable gates run at the **start of every phase**, before any migration, table, tool, or endpoint is written. They exist because the first cut of Phases 1–2 was built greenfield and only checked the live Supabase *afterward* — where `businesses` and `billing_accounts` already exist, populated. Checking what is already present is not optional and not a one-time step; it is the first action of each phase.

### Gate 1 — Inspect what already exists (repo AND backend), then replace / extend / isolate (never parallel)

1. **Backend.** Introspect the real `DATABASE_URL`/`MIGRATION_DATABASE_URL` schema (`information_schema`, `to_regclass`, row counts) for every table/enum/function the phase would create or touch. Assume populated polsia2 tables until proven otherwise.
2. **Repo.** Read the canonical trunk's live model (the SQLite `TakyonStore` in `core.py`) and the existing migrations under `plugins/takyon/db/migrations/`; treat polsia2's migrations as prior art. Know what the runtime reads *today* vs. what is test-only.
3. **Decide, per overlap, exactly one — and write it down before any DDL:**
   - **Extend** the existing table/feature in place.
   - **Replace** a *disposable* polsia2 table deliberately (a migration that drops/renames it) — only after proving it is dead and unreferenced.
   - **Isolate** new tables in a dedicated `takyon` schema so names cannot collide with `public`.
   A new `public.<name>` that shadows a populated polsia2 `public.<name>` is none of these — it is the forbidden third parallel system.
4. **State the finding in the phase**: what exists, what it overlaps, and the chosen replace/extend/isolate decision. Never apply DDL to the shared, populated database without explicit operator confirmation.

### Gate 2 — Surface every new credential / provider need; never bandaid around it

If a phase needs a new secret, API key, service account, OAuth app, or paid provider to work for real, **STOP and tell the operator the exact need** — do not fake it, stub it silently, hardcode a placeholder, or otherwise bandaid around the missing credential to make something look done. For each such need, record **in this file**: the exact env var name(s), the provider/account required, the side effect it enables, the **test-mode** behavior (guarded/stubbed *with a receipt*), and the **live-mode** gate. If the capability works locally with no new credential, say so explicitly. A missing-credential path is `blocked` with a reason (invariant #8) — never a silent fallback or a fake "completed."

**Outstanding new-credential needs:**
- Phases 0–2: none (pure Postgres on the already-provisioned `DATABASE_URL`; Stripe keys already present, Connect deferred/optional).
- **Phase 3 — `STRIPE_BILLING_WEBHOOK_SECRET` (NEW, operator must provide).** Confirmed by the 2026-05-30 decision to use a SEPARATE control-plane webhook endpoint (not the shared product dispatcher) for flow-A topups. **Provisioning step (operator):** in the Stripe dashboard add a webhook endpoint pointing at `https://<control-api-host>/v1/billing/webhook`, subscribe it to `checkout.session.completed`, then copy that endpoint's signing secret (`whsec_…`) into `secrets/.env` as `STRIPE_BILLING_WEBHOOK_SECRET`. **Side effect it enables:** verified crediting of user→platform topups into the billing ledger (`billing.topup`). **Test mode:** tests inject a known secret + locally-signed payload (no real Stripe). **Live gate (invariant #8):** absent → `/v1/billing/webhook` returns `blocked`, never a fake credit. (The checkout-session *creation* needs only the already-present `STRIPE_SECRET_KEY`.)
- Later phases append here as needs are surfaced.

**Phase 3 gate finding (2026-05-30).** *Gate 2 (credentials):* `secrets/.env` already holds `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_PRICE_PLATFORM_MONTHLY`, `STRIPE_CONNECT_APPLICATION_FEE_BPS`. Flow-A **topups are ad-hoc amounts** (no fixed price id), so the topup checkout itself needs **no new credential** — *unless* it uses a SEPARATE Stripe webhook endpoint, which Stripe signs with a per-endpoint secret (a NEW `STRIPE_*_WEBHOOK_SECRET`). *Gate 1 (redundancy/conflict):* a complete Stripe checkout+webhook already exists in the SQLite trunk — `business_create_app_checkout` + `handle_business_record_stripe_webhook` (core.py:6808/6957), routed at `/api/webhooks/stripe` (app_api.py), deduped via `webhook_events`, written to `app_revenue_events`. **That is the PRODUCT / sub-user side (flow B-ish): a business's customers paying the business.** Phase 3 topup is **flow A (user→platform)** — genuinely new; the ledger primitive `billing.topup(conn,user_id,amount_cents,idempotency_key)` already exists (billing.py:138, idempotent on the Stripe event id). The control-plane topup checkout-session creation (needs only `STRIPE_SECRET_KEY`) and a Postgres-backed per-user rate limiter (no credential, must be PG-backed since workers are stateless) are buildable now. The open decision is the **webhook topology** (shared single endpoint dispatching by metadata = no new secret, but couples to/replaces the SQLite product webhook now vs. at Phase 5; OR a separate control-plane endpoint = clean isolation but a NEW per-endpoint webhook secret). Resolve before building the topup *crediting* path (a checkout with no crediting webhook = money taken, balance unchanged = forbidden fake-`completed`, invariant #8).

## Phased Rollout (each phase leaves the system working)

> Every phase begins by running **both Build Discipline gates above** (inspect-what-exists → replace/extend/isolate; surface-credential-needs). A phase is not "started" until its inspection finding and any new-credential needs are written down.

- **Phase 0 — DB layer:** Postgres access layer + migration runner; CI on real Postgres. *Accept:* runtime reads/writes Postgres; migrations idempotent.
- **Phase 1 — Identity spine (STEP 1):** `users`, `user_api_keys` (one-active invariant), `businesses.owner_user_id`, payout fields; resolver `api_key → user → businesses → policy`; seed fixtures; route `GET /v1/me` + `/v1/businesses` through it. *Accept:* a request resolves to exactly one user + their businesses before any provider/worker call; revoked key rejected; second active key refused.
- **Phase 2 — Two ledgers:** billing + custody, reserve-then-settle, allowance-first, exact topup, idempotency, reconciliation. *Accept:* costly action → correct entries; double-charge impossible under concurrency; allowance never shown as dollars; custody accrues without Connect.
- **Phase 3 — Control API:** opaque-key boundary + rate limiting + topup checkout + payout/Connect (deferred). *Accept:* full opaque boundary; per-user rate limit; zero-connection onboarding.
- **Phase 4 — Execution policy engine:** inline vs job vs cheaper vs blocked from allowance/topup/policy/estimate. *Accept:* features degrade gracefully under budget pressure instead of hard-failing.
- **Phase 5 — App runtime API:** sub-user auth/session/account/usage/checkout/generate/jobs; sub-user payments accrue to owner custody minus app fee; project gateway keys. *Accept:* all apps share rails; sub-user payment shows in owner custody; generated app never holds provider key.
- **Phase 6 — Worker plane + scheduled wakes:** jobs queue, idempotent, retries-with-budget, receipts; Modal for heavy jobs; `wake_schedules` + `pg_cron` dispatch enqueues due CEO wakes into the queue (no local ticker). *Accept:* heavy work runs as jobs; partial = blocked/failed; a due `wake_schedule` enqueues exactly one job and the worker drains it; a host outage fires one catch-up wake, not a backlog; **no SQLite/jobs.json/systemd cron path is reintroduced.**
- **Phase 7 — Externalize filesystem + no-fleet proof:** Storage-backed workspaces; second empty-disk runtime resumes; demote/retire VPS. *Accept:* second host resumes from Postgres+Storage; VPS disposable.
- **Phase 8 — Kill SQLite:** delete the SQLite authority path. *Accept:* no SQLite authority remains; all E2E green on Postgres.

**Do first:** Phase 0 + Phase 1 — the Postgres data layer and the identity spine + resolver with one read routed through it. Everything else plugs into that.
