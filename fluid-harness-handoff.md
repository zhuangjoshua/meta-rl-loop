# Handoff — usage-based billing + backend rails for generated businesses

Continuation prompt for a fresh session on this workspace (`/Users/Zygote/Downloads/takyon`,
runtime trunk `hermes-agent-main`). Read `AGENTS.md` first and follow its development and
deployment workflow as written; author skills/tools per `skills/takyon/HANDOFF/`; prefer
extending existing skills and modules over adding new ones.

## Context

We are upgrading the platform so its agent can build products like the three studied briefs
(`~/Downloads/briefs/{bazzly.ai,aipeekaboo.com,angelmatch.io}.md`) — real SaaS apps that need
their own backend code, third-party integrations, a database, scheduled jobs, and per-customer
usage billing, instead of today's fixed rails + static SPA.

The design lives in two docs in this workspace. Read both and re-verify their code anchors
against the live tree before relying on them:

- `fluid-harness-plan.md` — the architecture: R1 billing spine, R2 integration/egress rail,
  R3 backend rails, R4 operator improvements (deferred behind a go/no-go gate).
- `fluid-harness-impl.md` — the phased build plan. Phase 1 (R1) ships first.

A load-bearing simplification already baked into the docs: the existing usage gate
(`app_usage.py:512`, `reserve_usage`) already limits per-request AI cost to a per-customer,
plan-derived allowance, and `compose_plan` already guarantees that allowance fits inside the
plan price with margin. So per-request AI usage on `subscription`-shape businesses is already
correctly billed. R1 adds a persistent credit balance only for what that model cannot express
— purchased top-up credits, priced non-AI actions, an exact monthly window — plus two new
plan shapes (`metered_credits`, `metered_quota`) routed through the existing `money_shape`
validation, leaving current `subscription` businesses unchanged.

## First task: fold these five review corrections into both docs

1. **Cost attribution is three categories, not two.** Fix plan §2.0 and impl Phase 1e:
   - (a) per-customer, per-request (e.g. an AI draft) → debit the customer's balance at
     request time.
   - (b) per-customer, **scheduled** (a per-brand daily scan, a per-project poll) → also the
     customer's balance, debited when the scheduled job runs. This is metered exactly like
     (a), **not** amortized into the plan price — and it is what makes the `metered_quota`
     shape work (each run debits quota units).
   - (c) genuinely business-wide (the agent's own build usage; enrichment that serves every
     customer) → the business's own budget.
   `compose_plan` cannot amortize business-wide daily cost per customer — customer count is
   unknown at plan-design time. The correct design-time check is simply the existing margin
   rule applied to the quota: `quota × worst_case_cost_per_unit ≤ price × (1 − margin)`.

2. **Don't hardcode migration numbers.** The impl doc says 0063/0064/0065; there are already
   two `0060_*` files and another developer pushes concurrently. Assign the next free number
   at ship time, per phase.

3. **`app_connections` already exists** (`business_list_app_connections`, the `connections`
   rail, `app_connections.py`; see `core.py:119/424/14477`). R2 extends it — do not add a
   parallel connections table.

4. **Phase 0 uses the existing plugin hook, not R4 machinery.** The pre-charge check belongs
   in the existing Hermes `pre_tool_call` hook (invoked from `model_tools.py`) on the current
   loop. Reword Phase 0 so it doesn't imply the deferred R4 work is being pulled forward.

5. **Soften the "AI spend is already correct" claim with its caveats:** the current window is
   weekly with no rollover (a conservative monthly total; the exact monthly bucket arrives
   with the new balance), and it covers only `subscription` businesses — the `metered_*`
   shapes need the new balance path. Also two small correctness notes: the confirm-spend
   quote needs a signed value or short-lived row so it is genuinely single-use, and the
   timeout reaper's settle-at-reserve must be idempotent against a late real settle so a slow
   provider response can't be charged twice.

## Then: implement Phase 1 (R1)

The customer credit balance + the two new plan shapes, per `fluid-harness-impl.md` Phase 1
(as corrected above). Ground rules, all from the repo's own docs:

- Keep `subscription`-shape businesses behaviorally identical; only the new shapes route
  through the balance.
- Database changes are additive/nullable migration files applied through the tracked
  `takyon migrate` step, per `AGENTS.md`.
- Every priced action resolves its cost from the canonical pricing source and refuses when
  unpriced or when the balance is insufficient (the existing 402 response shape in
  `ai_gateway.py:610`).
- Tests go in `tests/plugins/`; run them via `scripts/run_tests.sh`.
- Ship through the workflow in `AGENTS.md` (outer repo, tracked deploys), and accept only on
  the standard fresh-business browser test: a paying test customer who runs out of monthly
  credits gets a clean out-of-credits response, buys a top-up, and continues.

Fanned-out build agents inherit the session model (omit the `model:` parameter).
