# Fluid Operator — implementation plan

> **CORRECTIONS FOLDED (2026-07-03).** The five handoff corrections plus correction 6 from the
> capability audit are folded in below. **Phase 1 (R1) is SUPERSEDED as designed**: the money
> plan now lives in `subuser-billing-plan.md` (+ its Amendments) and `general-apps-plan.md`
> §2.9 — extend `app_usage.py`; do NOT build `app_wallet.py`, `app_wallets`/`app_wallet_entries`,
> `action_costs`, `policy_hooks.py`, or the `metered_credits`/`metered_quota` shape names.
> R2/R3 shapes were audit-verified as parsimonious widenings and stand (with the connection-table
> correction in 2a).

Build plan for `fluid-harness-plan.md`. Every phase is a self-contained, shippable change that follows
the AGENTS.md rails: focused local checks → stage intended files → commit + push the outer repo →
`gh run watch` → tracked deploy scripts to the touched planes → fresh-business browser E2E. Migrations
are new files under `plugins/takyon/db/migrations/` — **assign the next free number at ship time, per
phase** (never hardcode: concurrent developers push migrations; two `0060_*` files already collided).
Additive/nullable, run via `takyon migrate` on the operator host before restart. New skills sync +
verify per AGENTS.md step 7.

Key simplification found while anchoring: the existing `reserve_usage` gate (`app_usage.py:512`) already
holds provider-COGS-µUSD against a per-subuser allowance (`user_monthly_limit_microusd`), and
`included_ai_budget_microusd` is already `≤ price×(1−margin)` by `compose_plan`'s margin invariant. So
per-request AI spend on `subscription`-shape businesses is already correctly billed — **with caveats**:
the current window is weekly with no rollover (a conservative approximation of the monthly total; the
exact entitlement-anchored monthly bucket ships with Phase 1), and it covers only `subscription`-shape
businesses. Two correctness notes for Phase 1: the confirm-spend quote must be a signed value or
short-lived row so it is genuinely single-use, and the timeout reaper's settle-at-reserve must be
idempotent against a late real settle so a slow provider response can't be charged twice (the existing
`_settle_or_hold` + reservation-key idempotency are the pattern).

---

## Phase 0 — the enforcement slice (lands with R1, ~2 days) — CORRECTED

The pre-charge check lands in the **existing Hermes `pre_tool_call` hook** (invoked from
`model_tools.py`) on the current loop — NOT a new hook plane, NOT `policy_hooks.py`, and not
deferred-R4 machinery pulled forward. The hook is an advisory fast-fail that calls the SAME
availability function the charging tool itself calls; the deterministic money authority remains
`reserve()` inside the tool. No new receipt store — charging calls already write a `UsageEvent`;
surface it in the tool result.

- Tests: hook refuses an over-budget call; hook is a no-op on read-only tools. Deploy: operator plane only.

---

## Phase 1 (R1) — SUPERSEDED: implement per `subuser-billing-plan.md` (WS1–4 + Amendments)

The wallet design above (1a–1g as originally written) is replaced. **Corrections applied:**

- **Correction 6 (ledger home):** extend `app_usage.py` — the plan doc's own §2.2/§7.1 ruling wins
  over the impl's `app_wallet.py` leaf. There is NO second reserve→settle→release engine:
  `app_usage_events` is already the per-(business, app_user, reservation_key) entries ledger with
  idempotency, reaper, and `_settle_or_hold`. New state shrinks to ONE persistent-grant table
  (0012-shaped, keyed business+app_user, SECURITY DEFINER mint from settled Stripe events only),
  consumed as overflow after the derived period allowance inside the existing reserve gate.
- **No `action_costs` table** — per-action worst-case costs live in `agent/usage_pricing.py` (the
  one pricing SSOT, per the CLAUDE.md rule); credit pricing composes as `per_unit` `CostBasis`
  through `plan_composition.py`.
- **No `metered_credits`/`metered_quota` shape names** — implement the already-declared
  `credit_packs` shape; quota enforcement reads the dormant `app_plan_policies.included_action_quota`
  column as a derived per-window count on the existing reserve path.
- **Correction 1 (three cost categories, not two):** (a) per-customer per-request → debit the
  customer at request time; (b) per-customer SCHEDULED (a per-brand daily scan) → also the
  customer's allowance, debited when the job runs — this is what makes quota plans work, NOT
  amortized into the plan price; (c) genuinely business-wide → the business's own budget.
  `compose_plan` cannot amortize business-wide daily cost per customer (customer count is unknown
  at design time); the design-time check is `quota × worst_case_cost_per_unit ≤ price × (1−margin)`.
- Window: entitlement-anchored monthly replaces `date_trunc('week')` ×7/30 — one window for all
  shapes, window + resolver in one commit (no per-shape window fork).
- Amendments (see `subuser-billing-plan.md`): business-funded acquisition grants (trials/referral —
  "nothing free" = "nothing UNFUNDED"), gift beneficiary attribution, order shape carries a tax
  obligation, `quantity_source` billing.
- Tests in `tests/plugins/`; acceptance: fresh business in the browser — exhaust → clean 402 with
  the plan's declared exhaustion CTA (upgrade or top-up) → continue.

---

## Phase 2 (R2) — one egress rail (~2 wk)

### 2a. Migration (next free number at ship time) — connections — CORRECTED
The credential connections registry is a **NEW `provider_connections` leaf**:
`{business, provider, auth_kind, host, credential_ref, scopes, status}` (+ optional `app_user_id`
subject scope for per-customer OAuth later). **`app_connections.py` is the social like/pass/block
rail and must NOT carry credentials** — "one connections table" means one per CONCERN, so do not
extend the social rail's table with `credential_ref` columns and do not add a second CREDENTIAL
registry beside this one. Credential material stays in the safebox store, referenced by
`credential_ref`.

### 2b. Safebox generic route (`safebox_provider_proxy.py` + `safebox_app.py`)
- `POST /v1/egress` generalizing the per-provider routes (`:505-546` is the template): capability-scoped
  to a business server-side; look up the connection, attach the credential for calls to the connection's
  own host only, meter (wallet if a customer request, business budget if shared), return key-free. Reserve
  → attach → call → settle, so an unfunded business can't reach the provider. Validate the destination host;
  refuse internal addresses.

### 2c. `ctx.egress` in the action sandbox (`app_actions.py`)
- Add `egress` to the action `ctx` (beside `generate`/`invokeAction`, ~`:145/173`): `ctx.egress(connection,
  {method, path, body})` → the `/v1/egress` route. Bump `_ACTION_RUNTIME_RAILS` to include the egress rail.

### 2d. Credential acquisition tool
- `business_request_credential(provider, scopes)` — emits a consent link / sign-on card on the
  `operator_approvals` mechanics (`readmodular.md` §5.3), parks the task, resumes when the connection goes
  active (callback or probe). One platform-owned verified Google OAuth app for GSC scopes; Composio/Nango
  behind the safebox for the long tail; Nylas for mailboxes.
- Tests: egress attaches the right credential and only to the connection host; unfunded business is
  refused before the call; a connection for provider-A can't be aimed elsewhere. Deploy: all three planes
  (safebox route runs on the safebox host — follow the AGENTS.md safebox runbook).

---

## Phase 3 (R3) — real backends (~2–3 wk)

### 3a. Per-business database
- Migration (next free number at ship time): a per-business Postgres schema + a role scoped to that schema; `business_db_migrate`
  tool runs additive-only DDL as that role (no destructive ALTER/DROP), replayed idempotently + receipted
  — the prod `takyon_migration` privilege split, per business. Start schema-per-business in the current
  Supabase; the `ctx.db` interface is drawn so a graduation to a dedicated Neon project is a later swap.
- `ctx.db` in the action sandbox (parameterized queries against the business schema only).

### 3b. Background jobs for ingestion
- New `jobs` kind(s) (e.g. `business_ingest`) with their own lane (`jobs.py:_LANE_SQL:77` derives a lane
  per kind, so a long crawl can't starve `ceo_wake`); a handler that runs a business's ingestion action.
  Scheduled actions (`app_action_schedules`) already cover cron; this adds the heavy/long lane.

### 3c. Inbound webhooks
- Route `/api/hooks/<business>/<source>/<token>` beside the Stripe/Shopify routes
  (`web_server.py:3331/3342`): token routes, the provider signature (safebox-held per-source secret)
  authenticates, dedup via the existing `webhook_events` machinery, dispatch into an action; handlers stay
  idempotent on event id.

### 3d. Public API + MCP per business
- Generic `api/<name>` rail inside the existing app-plane allowlist (session or `app_gateway_keys` auth),
  handlers = the business's own action files; an MCP rail exposing those actions as tools. This is the
  min-delta version of Bazzly's API/MCP and Peekaboo's API/CLI.

### 3e. Data acquisition (the funded workstream)
- Ingestion/enrichment/SERP-buy actions are shared-cost, gated against the business budget (Phase 1's
  shared plane). Named explicitly as the slow, expensive milestone — this is the actual moat for
  Angel-Match / Peekaboo-class businesses.
- Deploy: subuser plane (webhooks, api rail, ctx) + operator (migrate). Acceptance folds into Phase-4 gate.

---

## Phase 4 (R4) — stronger operator — DEFERRED behind the go/no-go gate

Only after **one business is built and running through Phases 1–3 on the current loop.** Then, in order:
CEO turns on the Agent SDK (sessions + hooks-as-policy, tools over MCP) → persistent per-business
workspace with idle suspend → Playwright browser + proof-of-work (screenshot/E2E) task closure → scoped
subagents + a second-model check before irreversible actions. Hermes stays the control plane throughout.

---

## Acceptance (the whole thing)

Build a **white-hat Peekaboo clone** as a brand-new business through the browser: a GSC connection via
R2; a daily 5-engine scan as an R3 ingestion job (shared cost) producing a snapshot data asset in the
per-business DB; `metered_quota` plan tiers with data-point caps via R1; API keys via R3d. Done when a
paying test customer exhausts the monthly quota and gets a clean 402 → top-up → resume, and the report
shows both `customer cost ≤ paid×(1−margin)` and `shared cost ≤ business funding`.

## Sequence + dependencies
Phase 0 → 1 (hard first) → 2 → 3 → (gate) → 4. Phase 2 needs Phase 1's metering; Phase 3 needs both;
Phase 4 needs a live business. Each phase ships and is usable on its own.
