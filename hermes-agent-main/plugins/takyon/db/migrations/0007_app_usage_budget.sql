-- 0007_app_usage_budget.sql
-- Phase 5 (increment c): product AI-spend BUDGET CAP + per-event usage ledger, with
-- ONE authoritative reserve-then-settle gate (replaces the SQLite two-path gate).
--
-- Builds on 0005 (sub-user identity) and 0006 (entitlements). Two business-scoped tables:
--   * app_budgets       — one row per business: the hard AI-spend cap for the current
--     metering period (calendar month, UTC). status='active' is the only state that
--     permits spend. This is the product's COMPUTE budget (flow distinct from the user's
--     billing ledger in 0002 — that is the Takyon operator's money; this caps what a
--     business's PRODUCT is allowed to spend on AI on behalf of its sub-users).
--   * app_usage_events  — append-a-row ledger of every spend, each carrying a lifecycle
--     `status`: reserved -> completed (settle) | failed | released. The budget gate counts
--     COMMITTED spend = Σ(estimate of still-`reserved` rows) + Σ(actual of `completed`
--     rows); failed/released rows count zero. `reservation_key` is the idempotency handle
--     threaded reserve->settle/release (UNIQUE per business), mirroring billing.py's
--     reservation_key.
--
-- WHY collapse to reserve-then-settle (the whole point of this increment): the SQLite
-- trunk gated product spend on TWO uncoordinated paths, and both are wrong under load:
--   1. an estimate PRE-CHECK in the old SQLite `/generate` route that reads a rendered budget mirror and
--      compares estimate>remaining but RESERVES NOTHING — pure read-then-act, so N
--      concurrent /generate calls all see the same remaining and all proceed (overspend);
--   2. an actuals RE-SUM at insert time (core.py:5362) that sums actual_cost only and
--      raises if it would exceed the cap — but it fires AFTER the provider was already
--      called and paid, so tripping it means refusing to RECORD spend that already
--      happened (the ledger then under-counts real cost — a money-truth violation,
--      mediationplan invariant #8).
-- The fix is billing.py's pattern (Phase 3) applied to the product budget: reserve()
-- holds the estimate atomically under the budget row lock (the ONE gate); settle()
-- records the real actual and NEVER re-checks the cap (truth is mandatory once money is
-- spent); release() frees the hold on the failure path. Deliberate divergence from
-- billing.py: settle records the true provider actual even if it slightly exceeds the
-- reserved estimate — the cap is enforced at reserve, and refusing to record real spend
-- would reintroduce the very under-count this increment removes.
--
-- Postgres port of the SQLite trunk's app_budgets / app_usage_events (core.py:3026-3034,
-- 3203-3224); the SQLite product path is the predecessor, retired in Phase 8. Shape
-- changes from the SQLite original:
--   * status becomes a CHECKED lifecycle on app_usage_events (reserved/completed/failed/
--     released) — SQLite left it free text because it only ever wrote 'completed'/'failed';
--     the reserve state is net-new and load-bearing for the gate.
--   * net-new `reservation_key` (UNIQUE per business) — the idempotency handle the
--     reserve->settle/release lifecycle needs; SQLite carried idempotency at the op
--     envelope, not on the row.
--   * bigint cost columns + timestamptz + uuid PKs + jsonb metadata (vs. SQLite int/text),
--     matching 0001-0006. bigint matches included_ai_budget_microusd in 0006.
--
-- Idempotent DDL: safe to run repeatedly. Clean `public` only (local test DB, or live
-- Supabase AFTER the polsia2 teardown).
--
-- REPLACE guard (robustness #1 — mediationplan.md): mirror 0001-0006. Both tables are
-- net-new to Postgres, but `create table if not exists` would SILENTLY bind to a
-- differently-shaped pre-existing table if one existed. Both takyon tables are
-- BUSINESS-scoped (they carry business_slug); any non-takyon table of these names would
-- not be. Fail loud in that case.
do $$
begin
    if to_regclass('public.app_budgets') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'app_budgets'
             and column_name  = 'business_slug'
       )
    then
        raise exception
            'public.app_budgets exists but is not the takyon shape (no business_slug). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
    if to_regclass('public.app_usage_events') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'app_usage_events'
             and column_name  = 'business_slug'
       )
    then
        raise exception
            'public.app_usage_events exists but is not the takyon shape (no business_slug). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

-- One budget row per business. The reserve gate takes `... for update` on this row to
-- serialize all concurrent reserves for the business, so the committed-spend aggregate it
-- computes over app_usage_events is consistent (no oversell). The row carries NO cached
-- usage counter — committed spend is always re-derived from the event ledger, so the
-- ledger is the single source of truth and settle/release need not touch this row.
create table if not exists app_budgets (
    business_slug        text primary key references businesses (slug) on delete cascade,
    status               text not null default 'active' check (length(status) > 0),
    hard_limit_microusd  bigint not null default 5000000 check (hard_limit_microusd >= 0),
    current_period_start timestamptz not null default date_trunc('month', now()),
    current_period_end   timestamptz not null
                             default (date_trunc('month', now()) + interval '1 month'),
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);

-- Append-a-row usage ledger. status lifecycle: reserved -> completed | failed | released.
-- estimated_cost is the pre-flight hold (counted while reserved); actual_cost is the real
-- provider spend recorded at settle (counted while completed). app_user_id is SET NULL on
-- sub-user delete so the spend record survives the customer.
create table if not exists app_usage_events (
    id                      uuid primary key default gen_random_uuid(),
    business_slug           text not null references businesses (slug) on delete cascade,
    app_user_id             uuid references app_users (id) on delete set null,
    app_user_tier           text,
    reservation_key         text not null check (length(reservation_key) > 0),
    purpose                 text not null default 'product_usage' check (length(purpose) > 0),
    route                   text not null default 'app' check (length(route) > 0),
    status                  text not null default 'reserved'
                                check (status in ('reserved', 'completed', 'failed', 'released')),
    estimated_cost_microusd bigint not null default 0 check (estimated_cost_microusd >= 0),
    actual_cost_microusd    bigint not null default 0 check (actual_cost_microusd >= 0),
    input_tokens            integer check (input_tokens is null or input_tokens >= 0),
    output_tokens           integer check (output_tokens is null or output_tokens >= 0),
    provider_request_id     text,
    provider                text,
    model                   text,
    error                   text,
    metadata                jsonb not null default '{}'::jsonb,
    created_at              timestamptz not null default now(),
    completed_at            timestamptz,
    updated_at              timestamptz not null default now(),
    unique (business_slug, reservation_key)
);

-- The gate's aggregate scans (business, created_at) within the current period and filters
-- on status; this index serves both the committed-spend SUM and per-business listings.
create index if not exists app_usage_events_business_period_idx
    on app_usage_events (business_slug, created_at, status);
