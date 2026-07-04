-- 0064_app_user_credit_grants.sql
-- Persistent per-customer credit grants (subuser-billing-plan WS2 — the credit_packs enabler).
--
-- ONE new table: a purchased, non-expiring µUSD balance per (business, app_user), consumed as
-- OVERFLOW after the entitlement-anchored monthly allowance INSIDE the existing reserve gate
-- (no second reserve engine — app_usage_events stays the only entries ledger; the grant rows
-- hold only the balance). Semantics, chosen so every path is race-safe and idempotent:
--
--   * MINT is funded-only: safebox_grant_app_user_credits is idempotent on (source, source_id)
--     — the caller is a signature-verified, event-deduped payment path (same trust shape as
--     safebox_insert_app_entitlement, migration 0041). There is NO promo/comp mint.
--   * DEBIT happens only at RESERVE, under the business budget row lock the gate already holds
--     (all reserves for a business serialize there, so read-sum + debit is atomic). The debited
--     slices are recorded on the event row's metadata as grant_holds — the event stays the
--     single source of truth for what was held.
--   * REFUND happens only on the reserved→finalized transition (settle / release / reaper),
--     which the existing finalized-status no-op already makes exactly-once. Refunds are pure
--     increments capped at the grant's original amount, so they can never race a reserve into
--     overspend (a reserve that reads a stale, smaller balance merely under-admits — safe).
--   * SETTLE refunds the TOP SLICE: the grant covered the spend above the monthly allowance, so
--     when actual < estimate the reduction comes off the grant hold first:
--     refund = least(hold, estimate - actual). RELEASE and the reaper refund the full hold.
--   * The overflow only ever applies when the caller supplied a concrete per-user limit (a paid
--     plan) — an unentitled caller still refuses at 0 before grants are consulted; grants widen
--     a funded customer's month, they never fund an unfunded one.
--
-- Privileges follow the 0038/0041 pattern: the table is reachable ONLY through SECURITY DEFINER
-- functions; runtime roles keep EXECUTE on the gate functions they already use, and the mint /
-- raw-param balance read are OWNER-ONLY (safebox/operator authority planes) until the checkout
-- webhook wiring lands with its own exact role grant — fail closed, never pre-granted.

create table if not exists app_user_credit_grants (
    id uuid primary key default gen_random_uuid(),
    business_slug text not null references businesses(slug) on delete cascade,
    app_user_id uuid not null,
    amount_microusd bigint not null check (amount_microusd > 0),
    remaining_microusd bigint not null check (remaining_microusd >= 0),
    source text not null,
    source_id text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (source, source_id),
    check (remaining_microusd <= amount_microusd)
);

create index if not exists app_user_credit_grants_live_idx
    on app_user_credit_grants (business_slug, app_user_id)
    where remaining_microusd > 0;

alter table app_user_credit_grants enable row level security;

-- ── mint (funded-only, idempotent) ───────────────────────────────────────────────────────

create or replace function safebox_grant_app_user_credits(
    p_business_slug text,
    p_app_user_id   uuid,
    p_amount_microusd bigint,
    p_source        text,
    p_source_id     text
)
returns table(grant_id uuid, remaining_microusd bigint, replayed boolean)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_exists boolean;
    v_id uuid;
    v_remaining bigint;
begin
    if p_amount_microusd is null or p_amount_microusd <= 0 then
        raise exception 'grant amount must be positive';
    end if;
    if coalesce(trim(p_source), '') = '' or coalesce(trim(p_source_id), '') = '' then
        raise exception 'grant source and source_id are required (funded-only mint)';
    end if;
    select exists (
        select 1 from app_users where business_slug = p_business_slug and id = p_app_user_id
    ) into v_exists;
    if not v_exists then
        raise exception 'unknown app user % for business %', p_app_user_id, p_business_slug;
    end if;
    select g.id, g.remaining_microusd into v_id, v_remaining
        from app_user_credit_grants g
        where g.source = p_source and g.source_id = p_source_id;
    if found then
        return query select v_id, v_remaining, true;  -- idempotent replay: one payment, one grant
        return;
    end if;
    return query
        insert into app_user_credit_grants
            (business_slug, app_user_id, amount_microusd, remaining_microusd, source, source_id)
        values (p_business_slug, p_app_user_id, p_amount_microusd, p_amount_microusd,
                p_source, p_source_id)
        returning id, app_user_credit_grants.remaining_microusd, false;
end;
$$;

-- ── balance read ─────────────────────────────────────────────────────────────────────────

create or replace function safebox_app_user_grant_balance(
    p_business_slug text,
    p_app_user_id   uuid
)
returns bigint
language sql
security definer
set search_path = public, pg_temp
as $$
    select coalesce(sum(remaining_microusd), 0)
      from app_user_credit_grants
     where business_slug = p_business_slug and app_user_id = p_app_user_id;
$$;

-- ── refund helper (shared by settle / release / reaper) ──────────────────────────────────
-- Refunds up to p_refund_microusd back onto the grants named in p_grant_holds (the jsonb the
-- reserve wrote), newest-hold-first, each increment capped at the grant's original amount.
-- Exactly-once is guaranteed by the CALLERS' reserved→finalized status guard, not here.

create or replace function safebox_refund_grant_holds(
    p_grant_holds jsonb,
    p_refund_microusd bigint
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_hold record;
    v_left bigint := coalesce(p_refund_microusd, 0);
    v_part bigint;
begin
    if v_left <= 0 or p_grant_holds is null or jsonb_typeof(p_grant_holds) <> 'array' then
        return;
    end if;
    for v_hold in
        select (elem->>'grant_id')::uuid as grant_id,
               (elem->>'microusd')::bigint as microusd
          from jsonb_array_elements(p_grant_holds) with ordinality as t(elem, ord)
         order by ord desc
    loop
        exit when v_left <= 0;
        v_part := least(v_left, v_hold.microusd);
        update app_user_credit_grants
           set remaining_microusd = least(amount_microusd, remaining_microusd + v_part),
               updated_at = now()
         where id = v_hold.grant_id;
        v_left := v_left - v_part;
    end loop;
end;
$$;

-- ── reserve gate: grant overflow above the anchored monthly allowance ────────────────────
-- Same signature as 0037/0063 (plain CREATE OR REPLACE). Body = 0063 verbatim plus the
-- overflow branch inside the per-subuser gate.

create or replace function safebox_reserve_usage(
    p_business_slug           text,
    p_estimated_cost_microusd bigint,
    p_reservation_key         text,
    p_app_user_id             uuid,
    p_user_monthly_limit_microusd bigint,
    p_app_user_tier           text,
    p_purpose                 text,
    p_route                   text,
    p_provider                text,
    p_model                   text,
    p_metadata                jsonb
)
returns safebox_usage_gate_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r              safebox_usage_gate_result;
    v_status       text;
    v_hard_limit   bigint;
    v_period_start timestamptz;
    v_rolled_status       text;
    v_rolled_hard_limit   bigint;
    v_rolled_period_start timestamptz;
    v_user_exists  boolean;
    v_user_committed bigint;
    v_committed    bigint;
    v_user_period_start timestamptz;
    v_user_period_end   timestamptz;
    v_shortfall    bigint := 0;
    v_grant_row    record;
    v_debit        bigint;
    v_holds        jsonb := '[]'::jsonb;
    v_meta         jsonb;
begin
    insert into app_budgets (business_slug) values (p_business_slug)
        on conflict (business_slug) do nothing;
    select status, hard_limit_microusd, current_period_start
        into v_status, v_hard_limit, v_period_start
        from app_budgets where business_slug = p_business_slug for update;
    update app_budgets set
        current_period_start = date_trunc('week', now()),
        current_period_end = date_trunc('week', now()) + interval '1 week',
        updated_at = now()
        where business_slug = p_business_slug and current_period_end <= now()
        returning status, hard_limit_microusd, current_period_start
        into v_rolled_status, v_rolled_hard_limit, v_rolled_period_start;
    if found then
        v_status := v_rolled_status;
        v_hard_limit := v_rolled_hard_limit;
        v_period_start := v_rolled_period_start;
    end if;

    if v_status is distinct from 'active' then
        r.refusal := 'budget_inactive';
        r.fig_status := v_status;
        return r;
    end if;

    select e.id, e.business_slug, e.app_user_id, e.app_user_tier, e.reservation_key, e.purpose,
           e.route, e.status, e.estimated_cost_microusd, e.actual_cost_microusd, e.input_tokens,
           e.output_tokens, e.provider_request_id, e.provider, e.model, e.error, e.metadata,
           e.created_at, e.completed_at
        into r.id, r.business_slug, r.app_user_id, r.app_user_tier, r.reservation_key, r.purpose,
             r.route, r.status, r.estimated_cost_microusd, r.actual_cost_microusd, r.input_tokens,
             r.output_tokens, r.provider_request_id, r.provider, r.model, r.error, r.metadata,
             r.created_at, r.completed_at
        from app_usage_events e
        where e.business_slug = p_business_slug and e.reservation_key = p_reservation_key;
    if found then
        return r;
    end if;

    if p_app_user_id is not null then
        select exists (
            select 1 from app_users where business_slug = p_business_slug and id = p_app_user_id
        ) into v_user_exists;
        if not v_user_exists then
            r.refusal := 'app_user_not_found';
            return r;
        end if;
    end if;

    -- Per-subuser gate over the customer's entitlement-anchored monthly window (0063), with
    -- persistent-grant OVERFLOW above the allowance (0064).
    if p_app_user_id is not null and p_user_monthly_limit_microusd is not null then
        v_user_period_start := v_period_start;
        select e.current_period_end
          into v_user_period_end
          from app_entitlements e
         where e.business_slug = p_business_slug
           and e.app_user_id = p_app_user_id
           and e.status in ('active', 'trialing')
           and lower(coalesce(e.tier, '')) not in ('', 'free', 'none', 'unentitled')
           and e.source <> 'openmeter'
         order by
           case lower(e.tier)
             when 'owner' then 0
             when 'paid' then 1
             when 'pro' then 1
             else 100
           end asc,
           e.updated_at desc
         limit 1;
        if v_user_period_end is not null
           and v_user_period_end > now()
           and v_user_period_end - interval '1 month' <= now() then
            v_user_period_start := v_user_period_end - interval '1 month';
        end if;
        select coalesce(sum(case
                when status = 'reserved' then estimated_cost_microusd
                when status = 'completed' then actual_cost_microusd
                else 0 end), 0)
            into v_user_committed
            from app_usage_events
            where business_slug = p_business_slug and app_user_id = p_app_user_id
              and created_at >= v_user_period_start;
        if v_user_committed + p_estimated_cost_microusd > p_user_monthly_limit_microusd then
            v_shortfall := v_user_committed + p_estimated_cost_microusd
                           - p_user_monthly_limit_microusd;
            -- Overflow: debit persistent grants (oldest first) for the slice above the
            -- allowance. All debits happen here, under the budget row lock, so the
            -- read-sum-then-debit sequence cannot race another reserve. Refunds elsewhere
            -- are pure increments and only make more available — never less.
            for v_grant_row in
                select id, remaining_microusd
                  from app_user_credit_grants
                 where business_slug = p_business_slug and app_user_id = p_app_user_id
                   and remaining_microusd > 0
                 order by created_at asc
                   for update
            loop
                exit when v_shortfall <= 0;
                v_debit := least(v_shortfall, v_grant_row.remaining_microusd);
                update app_user_credit_grants
                   set remaining_microusd = remaining_microusd - v_debit,
                       updated_at = now()
                 where id = v_grant_row.id;
                v_holds := v_holds || jsonb_build_array(
                    jsonb_build_object('grant_id', v_grant_row.id, 'microusd', v_debit));
                v_shortfall := v_shortfall - v_debit;
            end loop;
            if v_shortfall > 0 then
                -- Not fully coverable: roll back any partial debits (still inside this
                -- transaction's statement scope — but debits above were real updates, so
                -- undo them explicitly) and refuse with the exact figures.
                perform safebox_refund_grant_holds(
                    v_holds,
                    (select coalesce(sum((elem->>'microusd')::bigint), 0)::bigint
                       from jsonb_array_elements(v_holds) as elem));
                r.refusal := 'app_user_budget_exceeded';
                r.fig_user_limit_microusd := p_user_monthly_limit_microusd;
                r.fig_committed_microusd := v_user_committed;
                r.fig_requested_microusd := p_estimated_cost_microusd;
                return r;
            end if;
        end if;
    end if;

    if v_hard_limit is not null then
        select coalesce(sum(case
                when status = 'reserved' then estimated_cost_microusd
                when status = 'completed' then actual_cost_microusd
                else 0 end), 0)
            into v_committed
            from app_usage_events
            where business_slug = p_business_slug and created_at >= v_period_start;
        if v_committed + p_estimated_cost_microusd > v_hard_limit then
            -- Refuse at the pool ceiling: return any grant debits taken above.
            perform safebox_refund_grant_holds(
                v_holds,
                (select coalesce(sum((elem->>'microusd')::bigint), 0)::bigint
                   from jsonb_array_elements(v_holds) as elem));
            r.refusal := 'budget_exceeded';
            r.fig_hard_limit_microusd := v_hard_limit;
            r.fig_committed_microusd := v_committed;
            r.fig_requested_microusd := p_estimated_cost_microusd;
            return r;
        end if;
    end if;

    v_meta := coalesce(p_metadata, '{}'::jsonb);
    if jsonb_array_length(v_holds) > 0 then
        v_meta := v_meta || jsonb_build_object(
            'grant_holds', v_holds,
            'grant_hold_microusd',
            (select coalesce(sum((elem->>'microusd')::bigint), 0)::bigint
               from jsonb_array_elements(v_holds) as elem));
    end if;

    insert into app_usage_events
        (business_slug, app_user_id, app_user_tier, reservation_key, purpose, route,
         status, estimated_cost_microusd, provider, model, metadata)
        values (p_business_slug, p_app_user_id, p_app_user_tier, p_reservation_key, p_purpose,
                p_route, 'reserved', p_estimated_cost_microusd, p_provider, p_model, v_meta)
        returning id, business_slug, app_user_id, app_user_tier, reservation_key, purpose, route,
                  status, estimated_cost_microusd, actual_cost_microusd, input_tokens, output_tokens,
                  provider_request_id, provider, model, error, metadata, created_at, completed_at
        into r.id, r.business_slug, r.app_user_id, r.app_user_tier, r.reservation_key, r.purpose,
             r.route, r.status, r.estimated_cost_microusd, r.actual_cost_microusd, r.input_tokens,
             r.output_tokens, r.provider_request_id, r.provider, r.model, r.error, r.metadata,
             r.created_at, r.completed_at;
    return r;
end;
$$;

-- ── settle: top-slice refund when actual < estimate ──────────────────────────────────────
-- Same signature as 0037; body identical plus the grant-refund step before finalizing.

create or replace function safebox_settle_usage(
    p_business_slug        text,
    p_reservation_key      text,
    p_actual_cost_microusd bigint,
    p_input_tokens         integer,
    p_output_tokens        integer,
    p_provider_request_id  text,
    p_provider             text,
    p_model                text,
    p_metadata             jsonb
)
returns safebox_usage_gate_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r        safebox_usage_gate_result;
    v_status text;
    v_estimated bigint;
    v_event_meta jsonb;
    v_hold_total bigint;
    v_refund bigint;
begin
    select status, estimated_cost_microusd, metadata
        into v_status, v_estimated, v_event_meta
        from app_usage_events
        where business_slug = p_business_slug and reservation_key = p_reservation_key
        for update;
    if not found then
        r.refusal := 'unknown_reservation';
        return r;
    end if;
    if v_status in ('completed', 'failed', 'released') then
        select e.id, e.business_slug, e.app_user_id, e.app_user_tier, e.reservation_key, e.purpose,
               e.route, e.status, e.estimated_cost_microusd, e.actual_cost_microusd, e.input_tokens,
               e.output_tokens, e.provider_request_id, e.provider, e.model, e.error, e.metadata,
               e.created_at, e.completed_at
            into r.id, r.business_slug, r.app_user_id, r.app_user_tier, r.reservation_key, r.purpose,
                 r.route, r.status, r.estimated_cost_microusd, r.actual_cost_microusd, r.input_tokens,
                 r.output_tokens, r.provider_request_id, r.provider, r.model, r.error, r.metadata,
                 r.created_at, r.completed_at
            from app_usage_events e
            where e.business_slug = p_business_slug and e.reservation_key = p_reservation_key;
        r.is_noop := true;
        return r;
    end if;
    -- Grant top-slice refund (0064): the grant covered the spend ABOVE the monthly allowance,
    -- so a shrink from estimate to actual comes off the grant hold first. Runs exactly once —
    -- the finalized-status guard above never lets a second settle/release reach here.
    v_hold_total := coalesce((v_event_meta->>'grant_hold_microusd')::bigint, 0);
    v_refund := 0;
    if v_hold_total > 0 and p_actual_cost_microusd < v_estimated then
        v_refund := least(v_hold_total, v_estimated - p_actual_cost_microusd);
        perform safebox_refund_grant_holds(v_event_meta->'grant_holds', v_refund);
    end if;
    update app_usage_events set
        status = 'completed',
        actual_cost_microusd = p_actual_cost_microusd,
        input_tokens = coalesce(p_input_tokens, input_tokens),
        output_tokens = coalesce(p_output_tokens, output_tokens),
        provider_request_id = coalesce(p_provider_request_id, provider_request_id),
        provider = coalesce(p_provider, provider),
        model = coalesce(p_model, model),
        metadata = metadata || coalesce(p_metadata, '{}'::jsonb)
                 || case when v_refund > 0
                    then jsonb_build_object('grant_refund_microusd', v_refund)
                    else '{}'::jsonb end,
        completed_at = now(),
        updated_at = now()
        where business_slug = p_business_slug and reservation_key = p_reservation_key
        returning id, business_slug, app_user_id, app_user_tier, reservation_key, purpose, route,
                  status, estimated_cost_microusd, actual_cost_microusd, input_tokens, output_tokens,
                  provider_request_id, provider, model, error, metadata, created_at, completed_at
        into r.id, r.business_slug, r.app_user_id, r.app_user_tier, r.reservation_key, r.purpose,
             r.route, r.status, r.estimated_cost_microusd, r.actual_cost_microusd, r.input_tokens,
             r.output_tokens, r.provider_request_id, r.provider, r.model, r.error, r.metadata,
             r.created_at, r.completed_at;
    return r;
end;
$$;

-- ── release: full refund of the grant hold ───────────────────────────────────────────────

create or replace function safebox_release_usage(
    p_business_slug   text,
    p_reservation_key text,
    p_error           text,
    p_metadata        jsonb
)
returns safebox_usage_gate_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r          safebox_usage_gate_result;
    v_status   text;
    v_new_status text;
    v_event_meta jsonb;
    v_hold_total bigint;
begin
    select status, metadata into v_status, v_event_meta
        from app_usage_events
        where business_slug = p_business_slug and reservation_key = p_reservation_key
        for update;
    if not found then
        r.refusal := 'unknown_reservation';
        return r;
    end if;
    if v_status in ('completed', 'failed', 'released') then
        select e.id, e.business_slug, e.app_user_id, e.app_user_tier, e.reservation_key, e.purpose,
               e.route, e.status, e.estimated_cost_microusd, e.actual_cost_microusd, e.input_tokens,
               e.output_tokens, e.provider_request_id, e.provider, e.model, e.error, e.metadata,
               e.created_at, e.completed_at
            into r.id, r.business_slug, r.app_user_id, r.app_user_tier, r.reservation_key, r.purpose,
                 r.route, r.status, r.estimated_cost_microusd, r.actual_cost_microusd, r.input_tokens,
                 r.output_tokens, r.provider_request_id, r.provider, r.model, r.error, r.metadata,
                 r.created_at, r.completed_at
            from app_usage_events e
            where e.business_slug = p_business_slug and e.reservation_key = p_reservation_key;
        r.is_noop := true;
        return r;
    end if;
    -- No spend happened: the whole grant hold goes back (exactly once, per the guard above).
    v_hold_total := coalesce((v_event_meta->>'grant_hold_microusd')::bigint, 0);
    if v_hold_total > 0 then
        perform safebox_refund_grant_holds(v_event_meta->'grant_holds', v_hold_total);
    end if;
    v_new_status := case when p_error is not null then 'failed' else 'released' end;
    update app_usage_events set
        status = v_new_status,
        actual_cost_microusd = 0,
        error = coalesce(p_error, error),
        metadata = metadata || coalesce(p_metadata, '{}'::jsonb)
                 || case when v_hold_total > 0
                    then jsonb_build_object('grant_refund_microusd', v_hold_total)
                    else '{}'::jsonb end,
        completed_at = now(),
        updated_at = now()
        where business_slug = p_business_slug and reservation_key = p_reservation_key
        returning id, business_slug, app_user_id, app_user_tier, reservation_key, purpose, route,
                  status, estimated_cost_microusd, actual_cost_microusd, input_tokens, output_tokens,
                  provider_request_id, provider, model, error, metadata, created_at, completed_at
        into r.id, r.business_slug, r.app_user_id, r.app_user_tier, r.reservation_key, r.purpose,
             r.route, r.status, r.estimated_cost_microusd, r.actual_cost_microusd, r.input_tokens,
             r.output_tokens, r.provider_request_id, r.provider, r.model, r.error, r.metadata,
             r.created_at, r.completed_at;
    return r;
end;
$$;

-- ── reaper: refund grant holds on orphaned reserves ──────────────────────────────────────
-- Replaces 0037's bulk UPDATE with a row loop so each orphan's grant hold is returned before
-- the row is released. Same cutoff semantics, same terminal state, same return count.

create or replace function safebox_reconcile_held_usage(p_older_than_seconds bigint)
returns bigint
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_row record;
    v_count bigint := 0;
    v_hold_total bigint;
begin
    for v_row in
        select business_slug, reservation_key, metadata
          from app_usage_events
         where status = 'reserved'
           and created_at < now() - make_interval(secs => p_older_than_seconds)
           for update skip locked
    loop
        v_hold_total := coalesce((v_row.metadata->>'grant_hold_microusd')::bigint, 0);
        if v_hold_total > 0 then
            perform safebox_refund_grant_holds(v_row.metadata->'grant_holds', v_hold_total);
        end if;
        update app_usage_events set
            status = 'released',
            actual_cost_microusd = 0,
            error = coalesce(error, 'reconciled: orphaned reserved hold'),
            metadata = metadata || jsonb_build_object('reconciled', true)
                     || case when v_hold_total > 0
                        then jsonb_build_object('grant_refund_microusd', v_hold_total)
                        else '{}'::jsonb end,
            completed_at = now(),
            updated_at = now()
         where business_slug = v_row.business_slug and reservation_key = v_row.reservation_key;
        v_count := v_count + 1;
    end loop;
    return v_count;
end;
$$;

-- ── privileges (0038/0041 pattern) ──────────────────────────────────────────────────────

revoke all on app_user_credit_grants from public;

-- FAIL-CLOSED privileges (subusers are hostile): NO runtime role gets the mint, the raw-param
-- balance read, or the refund helper — they run only under the privileged owner connection
-- (implicit execute), i.e. the safebox/operator authority planes. The gate functions above keep
-- their existing 0037 grants (takyon_app) via CREATE OR REPLACE. A session-scoped balance port
-- (business + session_hash, 0047-style — never raw app_user params an app-plane caller could
-- aim at another tenant) ships WITH the customer-facing top-up surface, not before.
revoke execute on function safebox_grant_app_user_credits(text, uuid, bigint, text, text) from public;
revoke execute on function safebox_app_user_grant_balance(text, uuid) from public;
revoke execute on function safebox_refund_grant_holds(jsonb, bigint) from public;
