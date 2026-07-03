-- 0060_money_shape_and_operator_approvals.sql
-- UC4 (modularization plan §2.7) — the money-shape gate + the minimal operator-approval rail.
--
-- Two additive, non-destructive changes, both OPERATOR/CEO-plane (plan authoring, not subuser
-- auth):
--
--   1. `businesses.money_shape` — the minimal per-business money-shape record (plan Q12: UC4 owns
--      a minimal record now; the archetype registry subsumes it later, when `money_shape` becomes a
--      derived attribute of `businesses.archetype`). One of 'subscription' (recurring plans — the
--      default), 'credit_packs', 'cogs_passthrough'. NULL means "not yet declared" and is read as
--      the default 'subscription' by the runtime — so every existing business keeps behaving exactly
--      as today (subscription plans allowed) with no backfill and no destructive migration.
--
--   2. `operator_approvals` — the minimal approval affordance (archetypes §1.5 spec: idempotent on
--      (business, action_kind, payload_digest), status pending|approved|denied|expired, TTL-bounded,
--      single-consume, receipted). Built once here; the archetypes plan EXTENDS this same table
--      (store-submission / sample-order consumers) rather than building a second one. UC4's only
--      consumer today is a money-SHAPE change.
--
-- SUBUSER-SECURITY INVARIANT (unchanged): nothing here touches subuser auth, entitlements, sessions,
-- or the money ledgers. `operator_approvals` is operator-plane only — the subuser/app runtime role is
-- explicitly denied. The `businesses` column is additive; the app-runtime business view
-- (`takyon_app_runtime_business`) is NOT changed to expose it, so the subuser plane cannot read it.

begin;

-- 1. Minimal per-business money-shape record. Additive nullable column + CHECK constraint on the
-- known shapes. NULL is legal and read as the 'subscription' default (no backfill required).
alter table public.businesses
    add column if not exists money_shape text;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'businesses_money_shape_chk'
          and conrelid = 'public.businesses'::regclass
    ) then
        alter table public.businesses
            add constraint businesses_money_shape_chk
            check (money_shape is null
                   or money_shape in ('subscription', 'credit_packs', 'cogs_passthrough'));
    end if;
end $$;

-- 2. The minimal operator-approval rail (archetypes §1.5). Idempotent on the payload digest so a
-- re-request of the SAME change returns the same pending record; single-consume is enforced in
-- application code (status flip pending->consumed under the unique key).
create table if not exists public.operator_approvals (
    id             uuid primary key default gen_random_uuid(),
    business_slug  text not null references public.businesses(slug) on delete cascade,
    action_kind    text not null,
    payload_digest text not null,
    status         text not null default 'pending'
                   check (status in ('pending', 'approved', 'denied', 'expired', 'consumed')),
    requested_at   timestamptz not null default now(),
    decided_at     timestamptz,
    consumed_at    timestamptz,
    actor          text,
    expires_at     timestamptz,
    receipt_path   text,
    metadata_json  jsonb not null default '{}'::jsonb,
    -- Idempotent on (business, action_kind, payload_digest): approving THIS payload, once.
    unique (business_slug, action_kind, payload_digest)
);
create index if not exists operator_approvals_business_idx
    on public.operator_approvals (business_slug);
create index if not exists operator_approvals_pending_idx
    on public.operator_approvals (business_slug, action_kind, status);

-- ---------------------------------------------------------------------------
-- GRANTS — operator + safebox + migration only. Subuser/app runtime EXPLICITLY denied
-- (the approval rail is CEO/operator plane; the subuser plane must never read or mint approvals).
-- ---------------------------------------------------------------------------
do $$
declare
    wr text;
begin
    revoke all on table public.operator_approvals from public;
    if exists (select 1 from pg_roles where rolname = 'takyon_app_runtime') then
        revoke all on table public.operator_approvals from takyon_app_runtime;
    end if;
    foreach wr in array array['takyon_operator_runtime', 'takyon_safebox_authority', 'takyon_migration', 'takyon_runtime'] loop
        if exists (select 1 from pg_roles where rolname = wr) then
            execute format('grant select, insert, update, delete on table public.operator_approvals to %I', wr);
        end if;
    end loop;
end $$;

commit;
