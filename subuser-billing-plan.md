# Subuser Billing Generalization — Plan

Replaces fluid-harness R1 (Phase 1) with a general, minimal-delta design. Scope: the subuser
(product app customer) money plane only. R2 (egress) and R3 (backends) are unchanged and out of
scope here. Line anchors are pointers, not contracts — re-verify against the live tree.

## Why this shape

The fluid-harness R1 design generalized one brief's billing mechanic (Bazzly top-ups) into a
three-table wallet engine — a third reserve→settle→release implementation beside
`app_usage.py` and `business_credits.py`, plus a second pricing table (`action_costs`).
Instead: classify the whole plan space once, span it with the smallest primitive set, and
extend the existing gate. Per `modular.md`'s golden rule — registry entries and declarations,
never a parallel path or per-type branch.

## The plan-type space

Any subuser plan decomposes on four axes:

| Axis | Values |
|---|---|
| Funding | recurring subscription · seats (quantity) · one-time purchase · prepaid PAYG · passthrough |
| Allowance | feature-gated · per-period allowance (expires) · persistent purchased balance · per-period quota |
| Unit | µUSD of AI cost · abstract credits · domain units (exports, scans, sends, bytes) · actions |
| Exhaustion | hard 402 · upgrade CTA · top-up · auto-top-up · (postpaid overage — deferred to OpenMeter) |

**Reduction:** every type is one of **two grant primitives** — a **period grant** (expires at
period end; funded by the subscription price) or a **persistent grant** (purchased; never
expires) — in a declared **unit**, with an **exhaustion policy**. The period grant already
exists (today's derived allowance). The pricing-side classification already exists
(`compose_plan` `CostBasis`: `metered` / `fixed` / `per_unit`). Media bytes already
reimplemented "period grant in unit=bytes" ad hoc — evidence the seam is right.

Coverage check: subscription = period grant (µUSD). Credits plan (Bazzly) = period grant
(credits) + persistent grant + auto-top-up. Quota plan (Peekaboo/AngelMatch) = period grant
(domain unit). Credit packs / one-time products = persistent grant alone. Seats = Stripe
subscription quantity priced `per_unit` at compose time.

## What already exists (do not rebuild)

- Fail-closed per-customer gate: `app_usage.py` reserve→settle→release — atomic row-lock
  refusal, idempotent reservation keys, TTL reaper, `_settle_or_hold` (never forget paid
  provider spend), structured 402s with exact figures (`ai_gateway.py:610`).
- Non-AI actions already meter through it (Tavily, DataForSEO, action invokes, email, media)
  priced via `agent/usage_pricing.py` `request_cost`; unpriced ⇒ refused before reserve.
- Plan choke point: `money_shape.assert_write_matches_shape` + `upsert_plan_policy` —
  free tiers already refused, monthly-only, budget ≤ price cap, grandfather freeze.
- Margin invariant: `compose_plan` enforces COGS ≤ price × (1 − margin), fail-loud, rounds
  price up, never clamps.
- Subuser payments: `app_payments.py` checkout, webhook dedup (`webhook_events`), renewals,
  dunning; SECURITY DEFINER safebox-only ledger writes (migrations 0037/0038/0041/0048).
- Dormant surfaces to reuse: `credit_packs` money shape (declared, unimplemented),
  `app_plan_policies.included_action_quota` (written, never read), `compose_plan`'s
  `credits` grant key (persisted, no runtime consumer).

## The delta (4 workstreams)

### 1. Generalize the one gate: unit + window
- Meter a declared `unit` per allowance (µUSD is the default; credits/actions/bytes/domain
  units are the same code path). Quota enforcement = period grant in unit ≠ µUSD, read from
  `included_action_quota`. Fold the media-bytes special case in.
- Entitlement-anchored **monthly** window replaces ISO-week × 7/30 — one window for all
  shapes, cut over in one commit (window + resolver together, per migration 0035's history).
  No per-shape window branch.
- Every reserve holds **worst-case** cost before the provider call; settle actual after.

### 2. The persistent grant (the one new primitive)
- One table: `(business_slug, app_user_id, unit, remaining, …)` + append-only entries reusing
  `app_usage_events` semantics. **No `app_wallet.py` engine** — consumed as overflow after the
  period grant inside the existing reserve ("monthly spends first" falls out for free).
- **Funded-only:** a grant can only be minted by a SECURITY DEFINER function from a settled
  Stripe event on the `app_payments` subuser webhook (failed payment mints nothing). No promo
  kind, no comp/gift path — every unit of spendable value traces to a payment.
- **Re-rate at spend:** persistent grants outlive their pricing; at reserve, re-check the
  unit's worst-case cost against current `usage_pricing.py`. If drift breaks margin, refuse
  and require repricing — never serve at a loss.
- Implements the declared `credit_packs` shape; do not add a `metered_credits` twin.
- Customer-facing surfaces (buy flow, balance read) = `RailRoute` additions to the existing
  checkout/usage rails in `RUNTIME_RAILS` (registry entry + keyed handler, selected via
  `runtime_features`; never widen the role allowlist).

### 3. Plans as declarations that cannot compose free or unprofitable
- A plan declares `{allowances: [(unit, qty)], purchasable: bool, exhaustion: block |
  upgrade | topup | auto_topup}`. Runtime has **zero plan-type conditionals** — it reads the
  declaration.
- `compose_plan` derives the price: Σ (qty × worst_case_unit_cost) ≤ price × (1 − margin),
  worst-case costs resolved only from `usage_pricing.py` (unpriced ⇒ `UnpricedAllowance`
  refusal). Persistent-grant unit prices pass the same check
  (unit_price ≥ worst_case_cost / (1 − margin)).
- **No-free / no-loss is a type invariant at the plan choke point**, not a review rule: a
  free, under-margin, or unpriced composition is unconstructable. No `action_costs` table —
  per-action worst-case costs live in `usage_pricing.py`.

### 4. Attribution + shared spend + docs
- Spend attribution on the reserve call (correction 1's three categories):
  **requester** (debit at request) · **scheduled-on-behalf-of-customer** (debit that
  customer's grants when the job runs; paid entitlement required — the existing
  `subscription_required` refusal stays for unattributed runs) ·
  **business-shared** (gated against the business's own real funding; never a free pool —
  invariant 9 stands).
- Fold handoff corrections 1–5 into `fluid-harness-plan.md` / `fluid-harness-impl.md`, plus
  **correction 6**: ledger home = extend `app_usage.py`; delete `app_wallet.py`,
  `app_wallet_grants`/`app_wallet_entries` (as designed), `action_costs`, and
  `policy_hooks.py` (pre-charge check = existing `pre_tool_call` hook in `model_tools.py`,
  calling the same availability function the charging tool calls).

## Amendments from the capability audit (2026-07-03 — see general-apps-plan.md §2.9)

The 383-capability audit falsified two claims and added four mechanics. WS1–4 stand as
written; these amendments layer on top:

- **"Nothing free" is corrected to "nothing UNFUNDED."** All three source briefs run trials
  (Peekaboo 14-day no-card, AngelMatch 3-day, Bazzly free browse tier) that the funded-only
  chain as written refuses — the coverage check over-claimed. Fix: **business-funded
  acquisition grants** — a second SECURITY DEFINER mint source drawing on the business's own
  settled funding ledger, declared and bounded `{days, card_required, allowance}` (trials,
  referral rewards, lead-magnet budgets). Every unit of value still traces to a settled
  payment — the customer's or the business's. The payer is generalized, never faked.
  Perpetual free plans and UNfunded comps stay unconstructable.
- **Gifts are funded**: payer ≠ beneficiary attribution on grants/orders; the refusal
  narrows to unfunded comps only.
- **Order shape** (one-time purchases): capture policy immediate|manual≤7d|deposit+balance,
  returns/dispute state machine, never an `app_plan_policies` row — and a **mandatory tax
  posture obligation** (Stripe Tax + collected-vs-MoR stance); compose refuses order-shape
  or cross-border composition without it.
- **Quantity billing generalizes seats**: declared `quantity_source` (org members |
  projects | brands | client workspaces — Bazzly bills per project, Peekaboo per brand),
  same `per_unit` compose path, synced to Stripe subscription_items, proration previewed
  via the quote path.
- **Rate tiers**: plan-declared rpm/day is an entitlement field; effective limit =
  min(plan tier, platform abuse policy) at the one `rate_limit.py` enforcement point.
- **Quote/confirm + autopilot envelope** on the reserve gate (consume-once quote_id;
  auto-confirm below N with a pace cap) — confirm-spend UX is a money-plane declaration.

## Loss-safety invariant chain

priced fail-closed → margin-checked at compose → grant only from settled payment →
worst-case reserve → settle actual → persistent units re-rated at spend.
Every link refuses rather than degrades. Nothing free: no plan without a price, no grant
without a payment, no spend without a paid entitlement.

## Explicitly not building

- The grants engine (burn-order machinery, expiring grant kinds, `promo`) — no brief needs
  it; even Bazzly is period-grant + persistent-grant (its monthly credits expire, no
  carryover).
- Postpaid overage / metered invoicing — deferred to OpenMeter. Prepaid auto-top-up covers
  the need.
- Annual / one-time billing intervals — monthly-only stands.
- A second reserve/settle/release implementation, a second pricing table, a second hook
  plane, a parallel connections table.

## Merge rules (blocking)

1. One gate: no second reserve/settle/release implementation anywhere.
2. One pricing SSOT: `usage_pricing.py`; hand-entered price rows are refused in review.
3. Zero plan-type conditionals in runtime code — new plan types must be expressible as
   declarations or they are refused.
4. No code path creates spendable value without a settled payment.
5. Migration numbers assigned at ship time, one migration per phase, additive/nullable only,
   applied via `takyon migrate` before service restart.

## Acceptance

Standard fresh-business browser E2E (new business, both hosts deployed): a paying test
customer on a declared-allowance plan exhausts it mid-period → clean 402 with reset date +
the plan's declared exhaustion CTA (upgrade or top-up) → completes that action → continues.
Plus: a scheduled on-behalf run debits the right customer; an unpriced-unit plan composition
is refused; a subscription-shape business behaves identically before/after except the
window cutover (called out in the deploy notes).
