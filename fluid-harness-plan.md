# Fluid Operator — making Takyon able to build businesses like Bazzly / Peekaboo / Angel Match

**PLAN ONLY — nothing here is implemented.** Companion to `modular.md` (seam catalog) and
`readmodular.md` (App Store rail). Follows the same house rules: ride canonical seams, keep money
gated, no second dispatcher, per-business look/logic stays in per-business state. Anchors checked
against the live tree 2026-07-03; the code is truth.

> **CORRECTIONS FOLDED (2026-07-03).** The handoff's five corrections plus correction 6 are folded
> in below. **R1 as detailed in §2 is SUPERSEDED**: the money design now lives in
> `subuser-billing-plan.md` (+ Amendments) and `general-apps-plan.md` §2.9 — extend `app_usage.py`
> (no `app_wallet.py`, no `app_wallets`/`app_wallet_entries`, no `action_costs`, no
> `metered_credits`/`metered_quota` shape names; implement `credit_packs` + the dormant
> `included_action_quota`). The overall framing (R1→R4, one egress rail, widen-the-sandbox, R4
> deferred) was audit-verified and stands; `general-apps-plan.md` is the authoritative capability
> model above all of it.

---

## 0. What these businesses need that Takyon can't do yet

Three real products were studied (`~/Downloads/briefs/{bazzly.ai,aipeekaboo.com,angelmatch.io}.md`).
They use different providers, but they are the **same shape** — the shape of essentially every useful
SaaS a founder builds:

| | Bazzly | Peekaboo | Angel Match |
|---|---|---|---|
| custom backend | Reddit ingest + reply queue + MCP | 5-engine prompt runner + scorer | investor DB + faceted search |
| acquired credentials | Reddit API, a comment supplier | GSC OAuth, 5 LLM providers, SERP | Nylas mailbox |
| background compute | continuous scan + autopilot | daily scans across engines | reply/open tracking |
| data asset | scored opportunities | daily visibility snapshots | 125K enriched investors |
| metered money | 100 credits/mo, top-ups | N data-points/mo, seats | export/send quotas |

Takyon today ships a **static React SPA + a fixed set of rails**. It can't give a business its own
backend code, its own credentials, its own database, or a per-customer spend meter. Adding provider
integrations one by one never catches up — the next business always needs the one you didn't add.

**So build the four things a human developer has, and let each business's specific integration live in
its own agent-written code** (exactly where a human puts it):

- **R1 — Money spine.** A per-customer credit/quota wallet so a customer can never cost the business
  more than they paid, plus a way to fund the business's own shared/build costs.
- **R2 — Egress rail.** One outbound path that attaches the right credential per call, so a business's
  code can call any API without ever holding a key.
- **R3 — Backend rails.** Widen the existing per-business action sandbox into a real backend: its own
  database, background jobs, inbound webhooks, and a public API/MCP.
- **R4 — Stronger operator.** Promote the Claude Agent SDK lane (already the coding worker) into the
  operator itself: a persistent workspace, a real browser, and "done" proven by a screenshot/test, not
  claimed.

Grounding from the research (2026): there's no off-the-shelf "human harness" to adopt — the strong
pattern is the Agent SDK's own primitives (persistent sessions, hooks, subagents, browser,
proof-of-work). Every serious multi-tenant platform (Cloudflare Outbound Workers, Vercel/Deno sandboxes,
Anthropic's vault+proxy) keeps provider keys out of tenant code by attaching them at an outbound proxy.
And every platform that enforces spend limits in real time keeps its own ledger — Stripe's credit grants
only settle at invoice time, so Stripe moves the money and our Postgres decides each request.

---

## 1. The seams that already exist (this is mostly widening, not new planes)

| Need | Today | Anchor |
|---|---|---|
| Per-business server code | **exists, narrow** — `product/site/actions/<name>.ts`, Deno sandbox, `ctx` = runtime client only | `app_actions.py:1297/1835` |
| Per-business cron | **exists** — scheduled action files, 15-min, on the `jobs` lanes | `app_actions.py:1211/1263` |
| Per-customer spend gate | **exists** — reserve→settle→release, fail-closed 402 | `app_usage.py:512/586/697`, `ai_gateway.py:382/610` |
| …but the window is | **weekly, plan-derived** (not monthly, no stored balance) | `app_usage.py:344`, `ai_gateway.py:362` |
| Plan money-shape choke point | **exists on prod** — subscription / credit_packs / cogs_passthrough | `money_shape.py:37-42/76` |
| Design-time margin check | **exists, fail-loud** (`COGS ≤ price×(1−margin)`) | `plan_composition.py:34-42` |
| Brokered provider calls | **exists, one route per provider** — no generic path | `safebox_provider_proxy.py:505-546` |
| Customer credits / top-ups | **don't exist** (`business_credits.py` is a different, business-side ledger) | — |
| Generic inbound webhook | **doesn't exist** (only platform Stripe/Shopify) | `app_payments.py:368` |
| Per-business database | **doesn't exist** (one DB, `business_slug` column + RLS) | `env_provisioner.py:279` |
| Claude SDK worker | **exists, key-free, product/site only** | `core.py:7666/7802` |

Three of the four rails are widenings of a seam that's already there. Net-new: the customer wallet, a
generic egress route, an inbound-webhook receiver, per-business DB isolation, and the operator promotion.

---

## 2. R1 — Money spine (build first; everything meters through it)

### 2.0 THREE cost categories, or the margin quietly goes negative (correction 1)

The operator's rule — *a customer can never cost more than they paid* — is only part of the picture.
Cost attribution is **three** categories, not two:

- **(a) Per-customer, per-request** — the AI draft a Bazzly user generates, the search a Peekaboo user
  runs. Debit that customer's allowance at request time.
- **(b) Per-customer, SCHEDULED** — a per-brand daily scan, a per-project poll. ALSO the customer's
  allowance, debited when the scheduled job runs. This is metered exactly like (a), **not** amortized
  into the plan price — and it is what makes quota plans work (each run debits quota units).
- **(c) Genuinely business-wide** — the agent's own build usage; enrichment that serves every customer.
  The business's own budget.

`compose_plan` cannot amortize business-wide daily cost per customer — customer count is unknown at
plan-design time. The correct design-time check is the existing margin rule applied to the quota:
`quota × worst_case_cost_per_unit ≤ price × (1 − margin)`. Shared cost (c) is funded by, and gated
against, the business's own budget — not a flat per-business cap (there deliberately isn't one, per
GOAL_RULES §3 / `app_usage.py:48`); if the business isn't funded for it, the shared job doesn't run.

### 2.1 Design time — reuse the money-shape choke point, don't fork it — CORRECTED

`money_shape.py` already forces every plan write into a declared shape — and it already declares
`credit_packs`, which has never been implemented. **Implement `credit_packs` instead of adding
`metered_credits`; enforce quota plans through the dormant `app_plan_policies.included_action_quota`
column instead of adding `metered_quota`.** No new shape names; the wallet rides the existing
`assert_write_matches_shape` gate by construction.

One credit unit; $1 = 1 credit (credits are presentation over µUSD). **No `action_costs` table** —
per-action worst-case costs are entries in `agent/usage_pricing.py` (the one pricing SSOT), and
credit/quota pricing composes as `per_unit` `CostBasis` through `plan_composition.py`, under the
existing `MarginFloorViolation` invariant. Because persistent (top-up/pack) credits don't expire,
re-check the per-credit budget against the current pricing table at spend time — if a provider gets
more expensive, refuse/require repricing rather than let the margin silently erode.

### 2.2 Runtime — extend `app_usage.py` — CORRECTED (this ruling wins; correction 6)

The ledger home is `app_usage.py`. There is **no second reserve→settle→release engine**:
`app_usage_events` already IS the per-(business, app_user, reservation_key) entries ledger with
idempotent reserve/settle/release, the TTL reaper, and `_settle_or_hold`. New state shrinks to ONE
persistent-grant table (0012-shaped, keyed `(business_slug, app_user_id)`, SECURITY DEFINER mint
from settled Stripe events only — plus, per the Amendments, bounded business-funded acquisition
grants), consumed as **overflow after the derived period allowance** inside the existing reserve
gate — "monthly spends first" falls out for free.

- Reserve worst-case under the existing row lock; if short, the existing non-retriable 402 with
  `{needed, available}` plus the plan's declared exhaustion CTA (`upgrade`/`topup`).
- Settle from real usage, refund the unspent reserve immediately; unknown outcomes settle at full
  reserve and reconcile later (err toward charging — `_settle_or_hold` already does this).
- Anchor the period to the subscription cycle (entitlement-anchored monthly), replacing
  `date_trunc('week')` ×7/30 — **one window for all shapes, window + resolver in one commit**; no
  per-shape window fork.

### 2.3 Stripe — reuse the control-plane checkout pattern

- Monthly grant only on `invoice.paid` (idempotent by invoice id → a failed payment simply grants nothing).
- Top-up = PaymentIntent with an idempotency key → non-expiring grant.
- Auto-top-up = off-session charge below a threshold; a decline disables auto-top-up and notifies.
- Seats = `subscription_items` quantity + `always_invoice` proration, previewed first — Bazzly's exact model.
- Stripe meters/grants are optional mirrors for reporting; Postgres decides each request.

### 2.4 Quotas, confirm-spend, autopilot — same machinery

- A per-feature quota wallet (data-points / exports / sends) is the same code with reserve = 1/unit —
  Peekaboo's quota and Angel Match's caps for free. Rate limits (5 rpm tiers) stay as cheap counters in
  front; the ledger is the money backstop.
- Confirm-spend: charging endpoints return `{credits, balance_after, quote_id}` on a dry run; the real
  call consumes the quote once. Bazzly's `confirmCreditSpend`.
- Autopilot = the agent confirms within a declared envelope (a below-N auto-confirm + a daily/pace cap),
  never a human per action — same gate, different confirmer.
- Nightly true-up: settled cost vs provider truth vs `paid × (1 − margin)`; a breach means a mispriced
  `action_costs` row — fix the row, the gate self-corrects.

---

## 3. R2 — One egress rail (so we stop adding providers one at a time)

A generic outbound route on the safebox, generalizing the per-provider ones that already exist
(`safebox_provider_proxy.py:505-546` is the template): a business's code calls
`ctx.egress(integration_id, {method, path, body})`; the safebox looks up that business's connection,
attaches the credential, meters the call (customer wallet when it's a customer request, business budget
when it's shared/ingestion; free calls still get a $0 receipt), and returns the response. **The
business's code never sees the key** — that's the whole point, and it's also why a business can safely
run agent-written code that calls third parties.

- **Connections** = a NEW `provider_connections` leaf `{business, provider, auth_kind, host,
  credential_ref, scopes}` (correction: `app_connections.py` is the social like/pass/block rail and
  must NOT carry credentials — one table per CONCERN). The
  credential is attached only for calls to that provider's own host, so a connection can't be pointed
  somewhere else. Reuse the platform's verified Google OAuth app for GSC-class scopes, Composio (already
  wired) or self-hosted Nango for the long tail, Nylas for mailboxes (Angel Match's rail).
- **Getting a credential** generalizes the sign-on card (`readmodular.md` §5.3, on `operator_approvals`):
  a `business_request_credential(provider, scopes)` tool emits a consent link, parks the task, and
  resumes when the connection goes active. Signups with no API run through the R4 browser and pause at
  the credential step; the credential lands in the safebox, never in the model's context.
- Meter before attaching the credential (reserve → attach → call → settle), so an unfunded business can't
  reach the provider. Standard hygiene: the safebox validates the destination host and refuses internal
  addresses; non-HTTP egress stays off (the sandbox already scopes `--allow-net` per host).
- Compliance note: some providers (Reddit's Nov-2025 builder policy, Google restricted scopes) require an
  approved, provider-specific app — those are set up once at the platform level, not shared informally.

---

## 4. R3 — Real backends (widen the action sandbox)

- **Keep the Deno sandbox; grow `ctx`:** `ctx.db` (the business's own schema), `ctx.egress` (R2),
  `ctx.wallet` (quote/meter), `ctx.enqueue/schedule`. Long ingestion (Reddit polling, SERP crawls,
  enrichment) becomes a `jobs` kind with per-business lanes — the queue and `ClaimScope` already exist.
- **Per-business database:** start with a schema per business + RLS in the current Supabase (uniform
  migrations, zero new infra). A `business_db_migrate` tool runs DDL as a role scoped to that business's
  schema, additive-only and receipted — the same privilege split as prod migrations, per business. Drawn
  so a business can later graduate to a dedicated Neon project (Neon's agent plan covers ~30k projects/mo
  free, and its project-transfer API makes handing a business to its owner clean).
- **The data asset is a funded milestone, not a free byproduct.** Angel Match's moat is years of
  aggregation; Peekaboo's is daily snapshots. Cold-start data buys / crawls / enrichment are shared cost
  (§2.0) — the build plan treats "acquire the data" as an explicit funded step, because it's the hardest
  and slowest part of all three.
- **Inbound webhooks:** per-business URLs `/hooks/<business>/<source>/<token>` where the token routes and
  the provider's signature (verified with the safebox-held per-source secret) authenticates; dedup via
  the existing `webhook_events` table; handlers stay idempotent on event id.
- **Public API + MCP per business:** scoped keys already exist (`app_gateway_keys`); add a generic
  `api/<name>` rail (session or key auth, inside the current allowlist) whose handlers are the business's
  own action files, plus an MCP rail exposing them as tools — Bazzly's API/MCP and Peekaboo's API/CLI, per
  business, for free.
- Later, if the sandbox's limits bind (heavy Python/ML), a second compute plane (Cloudflare Workers for
  Platforms, whose Outbound Workers give the same credential-attach behavior) slots behind the same `ctx`
  interface. Not needed for v1.

---

## 5. R4 — A stronger operator (promote the SDK lane)

Takyon already runs the Claude Agent SDK sandboxed and key-free; this is a promotion, not a rewrite.

**R4 is a separate track behind a go/no-go gate — R1–R3 must not depend on it.** R1–R3 run entirely on
the current CEO loop; only a thin slice lands early (PreToolUse hooks + receipts for the money and egress
gates, so those are enforced by code, not prompt discipline). The full promotion below is deferred until
a named exit criterion is met: **one business built and running through R1–R3 on the current loop**, at
which point we know what the operator actually needs from a stronger shell. This keeps the
highest-variance work from blocking the capability that pays for itself, and stops R4 scope-creeping
forward.

- **CEO turns run on the Agent SDK** — sessions that resume/fork, the event log as the receipt trail,
  `business_*` tools over MCP, and hooks that enforce the gates that are prose today (money shape, spend
  ceilings, publish blockers). Deterministic where it should be, agentic where it should be.
- **A persistent workspace per business** — shell + repo + browser that survives across sessions and
  suspends when idle (so idle compute isn't billed, and that compute is shared cost against the business
  budget). Handoffs use an initializer/incremental split with a feature ledger the agent advances but
  can't rewrite.
- **A real browser** (Playwright MCP in the worker image) for signups, provider consoles, and — the big
  one — proving work: no product or integration task closes without a screenshot or an end-to-end run
  attached. This also finishes the design self-review loop from the taste change.
- **Scoped subagents** plus a second-model check before anything irreversible (deploy, large spend,
  external send).
- Hermes stays the control plane (wakes, jobs, receipts, safebox); only the reasoning shell changes.
  Discovery stays skill/tool/gate-error driven — no hardcoded workflow router.

---

## 5.1 What this buys, and what it doesn't

This plan removes an **architectural ceiling** — after it, the agent is no longer *unable* to give a
business its own backend, credentials, database, background jobs, and a per-customer meter. It does not
by itself produce revenue. The hardest part of all three studied businesses is acquisition-side: Angel
Match's data asset took years to aggregate, Peekaboo shipped ~77 SEO posts, Bazzly depends on a supplier.
So the data-asset build is treated as its own funded workstream (§4), and distribution stays owned by the
existing `takyon-distribution` / SEO / channel skills. Read this as "the operator can now build these,"
not "building these is now easy." That honesty is the point — the ceiling is what we're fixing here.

## 6. Order, acceptance, non-goals

**Order: R1 (+ the thin R4 hook/receipt slice) → R2 → R3 → full R4.** Money first; R2 and R3 are each
useful alone; full R4 multiplies them. Rough effort R1 ~2wk · R2 ~2wk · R3 ~2-3wk · R4 ~2-3wk, in line
with `readmodular.md`.

**Acceptance** (the standard fresh-business browser gate): rebuild one brief end to end as a brand-new
business through the UI — a white-hat Peekaboo clone is the cleanest (daily cron across engines via the
egress rail, a snapshot data asset as shared cost, quota metering, plan tiers, API keys, a GSC
connection). Done = a paying test customer who runs out of monthly credits and gets a clean
402 → top-up → auto-top-up cycle, and a report showing both `customer cost ≤ paid × (1−margin)` and
`shared cost ≤ business funding`.

**Non-goals:** no provider-by-provider registry as the strategy; no Stripe grants or an LLM proxy as the
*primary* spend gate (invoice-time / eventually-consistent — fine as a secondary check only); no flat
per-business pool cap (shared cost is gated against real funding); no second dispatcher or per-business
Caddy; no new secrets on the sub-user plane. Where a provider needs a compliant, approved app (Reddit,
Google restricted scopes), set it up properly at the platform level rather than working around it.

## 7. New `modular.md` seams when landed
1. **Customer persistent grants** — one grants table consumed inside the existing `app_usage.py`
   gate, declared through the existing `credit_packs` shape + `included_action_quota` so it rides
   the plan-write choke point (corrected: no new shape names, no wallet engine).
2. **Shared-cost gate** — build/ingestion/data spend metered against the business budget; the design-time
   margin check sums both cost planes.
3. **Generic egress** — safebox `ctx.egress` + a connection table: any provider is one row, credential
   attached at the proxy, metered.
4. **Backend `ctx`** — db / egress / wallet / enqueue, plus `business_db_migrate`, `/hooks/*`, and
   `api/*`+MCP rails.
5. **Operator-on-SDK** — CEO shell = Agent SDK sessions + hooks + browser + proof-of-work receipts.

## 8. Open questions
1. Shared cost: funded straight from the operator's budget, or does each business carry a balance the
   operator tops up? (Decides who carries cold-start data-acquisition risk.)
2. Per-business DB: schema-in-Supabase now vs Neon-project-per-business — v1 default and graduation trigger.
3. Egress custody: self-host Nango behind the safebox vs lean on the already-wired Composio.
4. R4 scope: full CEO-on-SDK now, or R1–R3 + the thin hook slice on the current loop, then promote after
   the first business is live?
