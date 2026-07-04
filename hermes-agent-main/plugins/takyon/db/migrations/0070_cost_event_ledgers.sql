-- 0070_cost_event_ledgers.sql
-- Granular cost/log OBSERVABILITY ledgers — one per plane, per the operator ask:
-- "per task and company we need to be able to easily track costs for what happened at multiple
--  time horizons and granularities … all tool calls, llm spend … and the logs our agent gives us
--  … two different tables for operator and subuser".
--
-- These tables are debugging truth, NOT money authority. The money rails stay exactly where they
-- are (billing_entries / app_usage_events / business_creative_credit_entries); a cost event row
-- CORRELATES to its money row via reservation_key / job_id instead of duplicating reserve/settle.
-- Writers are best-effort: a failed event write must never block or alter a money path, so there
-- are no FKs here — rows must survive business/user deletion (they are the debugging record) and
-- must never make a delete fail.
--
-- Security posture (no new authority, strictly deny-by-default):
--   * operator_cost_events — operator/authority planes only. The app/subuser plane has NO grant,
--     NO policy, NO port. Same posture as billing_entries/operator_approvals.
--   * app_cost_events — the subuser plane can ONLY append through the SECURITY DEFINER port
--     takyon_app_record_cost_event (mirrors takyon_app_record_event), and cannot read the table
--     at all (tighter than app_usage_events, which grants scoped SELECT). Authority planes read.
--   * Both tables: RLS enabled + forced, authority-role GUC-bypass policies only, append-only for
--     runtime roles (SELECT+INSERT; no UPDATE/DELETE grant).

begin;

create table if not exists operator_cost_events (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    started_at timestamptz,
    -- who / where (no FKs by design: debugging rows outlive their entities)
    business_slug text,
    user_id uuid,
    job_id text,
    run_id text,
    session_id text,
    task_kind text,
    -- what
    event_kind text not null check (length(event_kind) > 0),  -- 'llm_call' | 'tool_call' | 'turn' | 'job' | 'log' | ...
    name text,                                                -- tool name / job kind / log source
    status text not null default 'ok' check (length(status) > 0),
    -- spend detail (microUSD, matching app_usage precision; NULL = not a spend event)
    provider text,
    model text,
    input_tokens bigint check (input_tokens is null or input_tokens >= 0),
    output_tokens bigint check (output_tokens is null or output_tokens >= 0),
    cache_read_tokens bigint check (cache_read_tokens is null or cache_read_tokens >= 0),
    cache_write_tokens bigint check (cache_write_tokens is null or cache_write_tokens >= 0),
    reasoning_tokens bigint check (reasoning_tokens is null or reasoning_tokens >= 0),
    cost_microusd bigint check (cost_microusd is null or cost_microusd >= 0),
    cost_status text,                                         -- usage_pricing CostStatus: actual|estimated|included|unknown
    reservation_key text,                                     -- correlates to billing_entries.reservation_key
    duration_ms bigint check (duration_ms is null or duration_ms >= 0),
    error text,
    payload jsonb not null default '{}'::jsonb
);

create index if not exists operator_cost_events_business_idx
    on operator_cost_events (business_slug, created_at desc);
create index if not exists operator_cost_events_job_idx
    on operator_cost_events (job_id, created_at) where job_id is not null;
create index if not exists operator_cost_events_created_idx
    on operator_cost_events (created_at);
create index if not exists operator_cost_events_user_idx
    on operator_cost_events (user_id, created_at desc) where user_id is not null;

create table if not exists app_cost_events (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    started_at timestamptz,
    business_slug text not null check (length(business_slug) > 0),
    app_user_id uuid,
    app_user_tier text,
    event_kind text not null check (length(event_kind) > 0),
    name text,                                                -- feature / action / log source
    status text not null default 'ok' check (length(status) > 0),
    route text,
    purpose text,
    provider text,
    model text,
    input_tokens bigint check (input_tokens is null or input_tokens >= 0),
    output_tokens bigint check (output_tokens is null or output_tokens >= 0),
    cache_read_tokens bigint check (cache_read_tokens is null or cache_read_tokens >= 0),
    cache_write_tokens bigint check (cache_write_tokens is null or cache_write_tokens >= 0),
    cost_microusd bigint check (cost_microusd is null or cost_microusd >= 0),
    cost_status text,
    reservation_key text,                                     -- correlates to app_usage_events.reservation_key
    provider_request_id text,
    duration_ms bigint check (duration_ms is null or duration_ms >= 0),
    error text,
    payload jsonb not null default '{}'::jsonb
);

create index if not exists app_cost_events_business_idx
    on app_cost_events (business_slug, created_at desc);
create index if not exists app_cost_events_reservation_idx
    on app_cost_events (reservation_key) where reservation_key is not null;
create index if not exists app_cost_events_app_user_idx
    on app_cost_events (app_user_id, created_at desc) where app_user_id is not null;

-- ---------------------------------------------------------------------------
-- Subuser append port (the ONLY app-plane write path; mirrors takyon_app_record_event's
-- trust posture: the runtime labels its own business, payload is sanitized, and the
-- function never lets malformed JSON raise back into the caller's request).
-- ---------------------------------------------------------------------------

create or replace function takyon_app_record_cost_event(
    p_business_slug text,
    p_event_kind text,
    p_name text,
    p_status text,
    p_route text,
    p_purpose text,
    p_provider text,
    p_model text,
    p_input_tokens bigint,
    p_output_tokens bigint,
    p_cache_read_tokens bigint,
    p_cache_write_tokens bigint,
    p_cost_microusd bigint,
    p_cost_status text,
    p_reservation_key text,
    p_provider_request_id text,
    p_app_user_id uuid,
    p_app_user_tier text,
    p_duration_ms bigint,
    p_error text,
    p_payload_json text,
    p_started_at timestamptz
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_business_slug text := trim(coalesce(p_business_slug, ''));
    v_event_kind text := trim(coalesce(p_event_kind, ''));
    v_payload jsonb;
    v_id uuid;
begin
    if v_business_slug = '' then
        raise exception 'business_slug is required';
    end if;
    if v_event_kind = '' then
        raise exception 'event_kind is required';
    end if;
    begin
        v_payload := coalesce(nullif(p_payload_json, ''), '{}')::jsonb;
    exception when others then
        v_payload := jsonb_build_object('unparsed_payload', left(coalesce(p_payload_json, ''), 2000));
    end;

    insert into app_cost_events (
        business_slug, event_kind, name, status, route, purpose,
        provider, model,
        input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
        cost_microusd, cost_status, reservation_key, provider_request_id,
        app_user_id, app_user_tier, duration_ms, error, payload, started_at
    ) values (
        v_business_slug, v_event_kind,
        nullif(left(coalesce(p_name, ''), 200), ''),
        coalesce(nullif(trim(coalesce(p_status, '')), ''), 'ok'),
        nullif(left(coalesce(p_route, ''), 200), ''),
        nullif(left(coalesce(p_purpose, ''), 200), ''),
        nullif(left(coalesce(p_provider, ''), 100), ''),
        nullif(left(coalesce(p_model, ''), 200), ''),
        -- clamp negatives to 0 but preserve NULL = "not applicable"
        case when p_input_tokens is null then null else greatest(p_input_tokens, 0) end,
        case when p_output_tokens is null then null else greatest(p_output_tokens, 0) end,
        case when p_cache_read_tokens is null then null else greatest(p_cache_read_tokens, 0) end,
        case when p_cache_write_tokens is null then null else greatest(p_cache_write_tokens, 0) end,
        case when p_cost_microusd is null then null else greatest(p_cost_microusd, 0) end,
        nullif(left(coalesce(p_cost_status, ''), 40), ''),
        nullif(left(coalesce(p_reservation_key, ''), 200), ''),
        nullif(left(coalesce(p_provider_request_id, ''), 200), ''),
        p_app_user_id,
        nullif(left(coalesce(p_app_user_tier, ''), 100), ''),
        case when p_duration_ms is null then null else greatest(p_duration_ms, 0) end,
        nullif(left(coalesce(p_error, ''), 2000), ''),
        v_payload,
        p_started_at
    )
    returning id into v_id;

    return v_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- Grants + RLS (deny-by-default; the loops mirror 0059/0062 conventions)
-- ---------------------------------------------------------------------------

do $$
declare
    tbl text;
    wr text;
    app_role text;
begin
    foreach tbl in array array['operator_cost_events', 'app_cost_events'] loop
        execute format('revoke all on table public.%I from public', tbl);
        -- explicitly deny the subuser/app plane any direct table access
        foreach app_role in array array['takyon_app_runtime', 'takyon_app'] loop
            if exists (select 1 from pg_roles where rolname = app_role) then
                execute format('revoke all on table public.%I from %I', tbl, app_role);
            end if;
        end loop;
        -- authority planes: append-only (SELECT + INSERT; no UPDATE/DELETE)
        foreach wr in array array['takyon_operator_runtime', 'takyon_safebox_authority', 'takyon_runtime'] loop
            if exists (select 1 from pg_roles where rolname = wr) then
                execute format('grant select, insert on table public.%I to %I', tbl, wr);
            end if;
        end loop;

        execute format('alter table public.%I enable row level security', tbl);
        execute format('alter table public.%I force row level security', tbl);
        foreach wr in array array['takyon_runtime', 'takyon_operator_runtime', 'takyon_safebox_authority', 'takyon_migration'] loop
            if exists (select 1 from pg_roles where rolname = wr) then
                execute format('drop policy if exists takyon_%s_guc_bypass on public.%I', wr, tbl);
                execute format(
                    'create policy takyon_%s_guc_bypass on public.%I for all to %I '
                    'using (takyon_rls_bypass()) with check (takyon_rls_bypass())',
                    wr, tbl, wr
                );
            end if;
        end loop;
    end loop;
end $$;

revoke execute on function takyon_app_record_cost_event(
    text, text, text, text, text, text, text, text,
    bigint, bigint, bigint, bigint, bigint, text, text, text,
    uuid, text, bigint, text, text, timestamptz
) from public;

do $$
declare
    r text;
begin
    -- the app plane appends ONLY through this port; authority planes may use it too so there is
    -- exactly one writer shape for app_cost_events.
    foreach r in array array['takyon_app_runtime', 'takyon_app', 'takyon_runtime', 'takyon_operator_runtime', 'takyon_safebox_authority'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format(
                'grant execute on function takyon_app_record_cost_event('
                'text, text, text, text, text, text, text, text, '
                'bigint, bigint, bigint, bigint, bigint, text, text, text, '
                'uuid, text, bigint, text, text, timestamptz) to %I',
                r
            );
        end if;
    end loop;
end $$;

commit;
