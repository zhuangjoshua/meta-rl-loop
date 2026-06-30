-- 0057_rl_ceo_memory_tables.sql
-- RL rails (rl-rails-plan.md) — operator-plane CEO memory + episode/trace/spend foundation.
--
-- SUBUSER-SECURITY INVARIANT (floor 5, "do not make subuser any less secure"):
-- Every table here is OPERATOR-PLANE only. The subuser/app runtime role (takyon_app_runtime)
-- is explicitly REVOKEd from all of them, so the subuser plane cannot read or write any CEO
-- memory. No grant on an existing subuser/money/identity table is added or changed by this
-- migration; it only CREATEs new operator-scoped tables. This is a membrane file and remains
-- VOID-by-default pending human sign-off before it is applied to the shared control plane.
--
-- Scope keys: every row carries operator_user_id (cross-operator isolation) + business_slug.

begin;

-- R1: episode rail — one bet the CEO made + how it settled + the re-derived reward.
create table if not exists public.ceo_episode (
    id              uuid primary key default gen_random_uuid(),
    operator_user_id text not null,
    business_slug   text not null references public.businesses(slug) on delete cascade,
    opened_at       timestamptz not null default now(),
    wake_id         text,
    hypothesis      text not null,
    channel         text,
    action_kind     text,
    baseline_json   jsonb not null default '{}'::jsonb,
    settle_horizon_seconds integer not null default 604800,   -- R3a: per-action settlement window (pinned default 7d)
    settled_at      timestamptz,
    outcome_json    jsonb,
    reward_numeric  numeric,                                  -- margin-net A(b); NULL until settled
    void_code       text,                                     -- floor 7 VOID catalog; NULL = scored
    fingerprint     text,                                     -- floor 4 dedupe (card fingerprint hash); NULL until anchor instrumented
    provenance_json jsonb not null default '{}'::jsonb,       -- source-liveness gate: which sources fed the reward
    created_at      timestamptz not null default now()
);
create index if not exists ceo_episode_business_idx on public.ceo_episode (business_slug);
create index if not exists ceo_episode_operator_idx on public.ceo_episode (operator_user_id);
create index if not exists ceo_episode_settled_idx on public.ceo_episode (settled_at);

-- R10: agent-trace rail — replayable turn-by-turn record of a wake.
create table if not exists public.ceo_trace (
    id              uuid primary key default gen_random_uuid(),
    operator_user_id text not null,
    business_slug   text not null references public.businesses(slug) on delete cascade,
    wake_id         text,
    turn_index      integer not null default 0,
    role            text not null,
    tool_name       text,
    content         text,
    created_at      timestamptz not null default now()
);
create index if not exists ceo_trace_business_wake_idx on public.ceo_trace (business_slug, wake_id, turn_index);

-- R5: identity — stable "who this CEO is" injected at every wake (one row per business).
create table if not exists public.ceo_identity (
    business_slug   text primary key references public.businesses(slug) on delete cascade,
    operator_user_id text not null,
    identity_md     text not null default '',
    updated_at      timestamptz not null default now()
);

-- R5: state-of-mind — "where I left off" written at wake-close; latest row wins at injection.
create table if not exists public.ceo_state_of_mind (
    id              uuid primary key default gen_random_uuid(),
    operator_user_id text not null,
    business_slug   text not null references public.businesses(slug) on delete cascade,
    state_md        text not null default '',
    created_at      timestamptz not null default now()
);
create index if not exists ceo_state_of_mind_latest_idx on public.ceo_state_of_mind (business_slug, created_at desc);

-- R3: per-business ad-spend rollup (margin-net denominator). Data already exists in Meta/Reddit
-- insights + business_ad_spend.last_synced_spend_cents + web_spend holds; this is the joinable sink.
create table if not exists public.business_ad_spend_entries (
    id              uuid primary key default gen_random_uuid(),
    operator_user_id text not null,
    business_slug   text not null references public.businesses(slug) on delete cascade,
    source          text not null,                            -- 'meta' | 'reddit' | 'web_spend' | ...
    provider_ref    text,                                     -- campaign/ad id for idempotent rollup
    spend_cents     bigint not null default 0,
    currency        text not null default 'USD',
    occurred_at     timestamptz,
    recorded_at     timestamptz not null default now(),
    metadata_json   jsonb not null default '{}'::jsonb,
    unique (business_slug, source, provider_ref, occurred_at)
);
create index if not exists business_ad_spend_entries_business_idx on public.business_ad_spend_entries (business_slug);

-- R6: interbusiness product-variation cohort (twins assigned only at create-time).
create table if not exists public.twin_cohort (
    id              uuid primary key default gen_random_uuid(),
    operator_user_id text not null,
    cohort_key      text not null,
    business_slug   text not null references public.businesses(slug) on delete cascade,
    variant_json    jsonb not null default '{}'::jsonb,
    created_at      timestamptz not null default now()
);
create index if not exists twin_cohort_key_idx on public.twin_cohort (operator_user_id, cohort_key);

-- ---------------------------------------------------------------------------
-- GRANTS — operator + safebox + migration only. Subuser/app runtime EXPLICITLY denied.
-- ---------------------------------------------------------------------------
do $$
declare
    t text;
    wr text;
begin
    foreach t in array array[
        'ceo_episode', 'ceo_trace', 'ceo_identity', 'ceo_state_of_mind',
        'business_ad_spend_entries', 'twin_cohort'
    ] loop
        -- Defense in depth: deny the subuser plane and PUBLIC regardless of default privileges.
        execute format('revoke all on table public.%I from public', t);
        if exists (select 1 from pg_roles where rolname = 'takyon_app_runtime') then
            execute format('revoke all on table public.%I from takyon_app_runtime', t);
        end if;
        -- Grant the operator-plane writers.
        foreach wr in array array['takyon_operator_runtime', 'takyon_safebox_authority', 'takyon_migration', 'takyon_runtime'] loop
            if exists (select 1 from pg_roles where rolname = wr) then
                execute format('grant select, insert, update, delete on table public.%I to %I', t, wr);
            end if;
        end loop;
    end loop;
end $$;

commit;
