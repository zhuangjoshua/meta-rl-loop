-- 0006_app_entitlements.sql
-- Phase 5 (increment b): product PLAN CATALOG + per-sub-user ENTITLEMENTS.
--
-- Builds on 0005 (sub-user identity). Two business-scoped tables:
--   * app_plan_policies — the per-business CATALOG of plans a product sells (price, tier,
--     included budget/quota, Stripe linkage). Descriptive: nothing here is a hard gate; the
--     enforced AI budget is app_budgets (increment c) and the effective access tier comes from
--     entitlements (below). UNIQUE(business_slug, plan_key).
--   * app_entitlements — append-a-row grants of access to a sub-user. The EFFECTIVE tier of a
--     sub-user is resolved from their entitlements whose status is active/trialing (highest
--     rank wins) and cached onto app_users.tier; this mirrors the SQLite `_sync_user_tier`.
--
-- Postgres port of the SQLite trunk's app_plan_policies / app_entitlements (core.py:3036-3140);
-- the SQLite product path is the predecessor, retired in Phase 8. Two deliberate shape changes
-- from the SQLite original:
--   * DROP the dead `stripe_payment_link_id` / `stripe_payment_link_url` columns — written by the
--     SQLite upsert (core.py:5203/5217-5218) but read NOWHERE, so they are cruft not ported.
--     (`included_action_quota` and `allow_overage` ARE kept: unlike the payment-link columns they
--     are read — rendered into product/plans.md at core.py:3884-3885 and feed the plan-validation
--     warnings at core.py:1995.)
--   * jsonb metadata + timestamptz + uuid PKs (vs. SQLite text), matching 0001-0005.
--
-- Idempotent DDL: safe to run repeatedly. Clean `public` only (local test DB, or live Supabase
-- AFTER the polsia2 teardown).
--
-- REPLACE guard (robustness #1 — mediationplan.md): mirror 0001-0005. Both tables are net-new to
-- Postgres, but `create table if not exists` would SILENTLY bind to a differently-shaped
-- pre-existing table if one existed. Both takyon tables are BUSINESS-scoped (they carry
-- business_slug); any non-takyon table of these names would not be. Fail loud in that case.
do $$
begin
    if to_regclass('public.app_plan_policies') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'app_plan_policies'
             and column_name  = 'business_slug'
       )
    then
        raise exception
            'public.app_plan_policies exists but is not the takyon shape (no business_slug). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
    if to_regclass('public.app_entitlements') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'app_entitlements'
             and column_name  = 'business_slug'
       )
    then
        raise exception
            'public.app_entitlements exists but is not the takyon shape (no business_slug). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

-- The plan catalog. One row per (business, plan_key). price_cents/budget are non-negative;
-- billing_interval is the canonical set the leaf normalizes aliases into. stripe_product_id /
-- stripe_price_id are COALESCE-preserved by the leaf's upsert (a re-upsert without them keeps the
-- prior linkage); every other field overwrites.
create table if not exists app_plan_policies (
    id                          uuid primary key default gen_random_uuid(),
    business_slug               text not null references businesses (slug) on delete cascade,
    plan_key                    text not null check (length(plan_key) > 0),
    tier                        text not null default 'free' check (length(tier) > 0),
    price_cents                 integer not null default 0 check (price_cents >= 0),
    currency                    text not null default 'usd' check (length(currency) > 0),
    billing_interval            text not null default 'month'
                                    check (billing_interval in ('month', 'year', 'one_time')),
    included_ai_budget_microusd bigint not null default 0 check (included_ai_budget_microusd >= 0),
    included_action_quota       integer not null default 25 check (included_action_quota >= 0),
    allow_overage               boolean not null default false,
    stripe_product_id           text,
    stripe_price_id             text,
    source                      text not null default 'takyon' check (length(source) > 0),
    notes                       text not null default '',
    metadata                    jsonb not null default '{}'::jsonb,
    created_at                  timestamptz not null default now(),
    updated_at                  timestamptz not null default now(),
    unique (business_slug, plan_key)
);

-- Per-sub-user entitlement grants. Append-a-row: each grant is its own row, and the sub-user's
-- effective tier is resolved across their rows (status active/trialing, highest rank). status is
-- left free text (Stripe statuses pass through; only active/trialing confer a tier). A non-free
-- 'manual' grant with no Stripe evidence is rejected by the leaf, not the DB — that money-truth
-- guard is enforced in app_entitlements.py.
create table if not exists app_entitlements (
    id                         uuid primary key default gen_random_uuid(),
    business_slug              text not null references businesses (slug) on delete cascade,
    app_user_id                uuid not null references app_users (id) on delete cascade,
    tier                       text not null default 'free' check (length(tier) > 0),
    status                     text not null default 'active' check (length(status) > 0),
    source                     text not null default 'manual' check (length(source) > 0),
    stripe_customer_id         text,
    stripe_subscription_id     text,
    stripe_checkout_session_id text,
    plan_key                   text,
    current_period_end         timestamptz,
    metadata                   jsonb not null default '{}'::jsonb,
    created_at                 timestamptz not null default now(),
    updated_at                 timestamptz not null default now()
);

create index if not exists app_entitlements_user_idx
    on app_entitlements (business_slug, app_user_id, status);
