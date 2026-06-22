-- 0037_safebox_ledger_boundary.sql
-- Ledger privilege boundary (Phase 1 of deploy/SAFEBOX-BROKER-REMEDIATION-PLAN.md): move the
-- product usage RESERVE/SETTLE/RELEASE row ops from open Python writes into SECURITY DEFINER
-- functions that the runtime can only EXECUTE, and lock down direct table writes.
--
-- WHY (the boundary this closes)
-- app_usage.reserve_usage/settle_usage/release_usage are the ONE reserve-then-settle gate on
-- product AI spend (0007). Today they INSERT/UPDATE app_usage_events directly. Whatever role the
-- runtime opens that write under therefore holds raw INSERT/UPDATE/DELETE on the ledger, so a
-- forged or stray write could fabricate or erase spend OUTSIDE the gate — the exact integrity hole
-- the safebox broker exists to remove. The fix is the same shape as 0030's role separation: make
-- the gate the ONLY writer. The reserve/settle/release logic becomes SECURITY DEFINER functions
-- owned by the migration role (the privileged runtime owner), executable by the runtime; direct
-- write privilege on app_usage_events is then REVOKED from the restricted `takyon_app` app-request
-- role (0030 granted it in error for a table only the gate should mutate). After this migration the
-- ONLY path that can write a usage row is the gate function — a caller can neither forge usage nor
-- erase it, even with a connection scoped to the app role.
--
-- VERBATIM PORT (correctness #1)
-- The function bodies are a 1:1 port of the Python row ops in plugins/takyon/app_usage.py — same
-- insert-on-conflict + select-for-update budget open, same weekly roll, same committed-spend
-- aggregate (Σ reserved estimates + Σ completed actuals in the period), same per-subuser and
-- per-business pool gates, same idempotent-on-reservation_key short circuit, same COALESCE-preserve
-- + metadata-merge on settle, same failed/released split on release. Refusals are signalled back to
-- Python via a `refusal` discriminator column carrying the EXACT figures the typed exceptions need
-- (AppBudgetInactive / AppUserBudgetExceeded / AppBudgetExceeded / AppUserNotFound /
-- UnknownReservation), so app_usage.py raises the identical exceptions with identical fields and
-- every existing test stays green. Billing math is unchanged — no estimate is capped, settle still
-- records the true provider actual even above the reserved estimate (the 0007 money-truth rule).
--
-- Idempotent: create-or-replace functions, guarded role create, repeatable grants/revokes,
-- create-if-not-exists for the safebox nonce table. Safe to re-run.

-- ── safebox role + single-use nonce store ───────────────────────────────────────────────
-- A minimally-privileged, NON-login, NON-superuser, NON-BYPASSRLS role that OWNS the broker's
-- single-use nonce store. Mirrors 0030's `takyon_app`: it is reached only via SET ROLE on an
-- already-authenticated safebox connection, never a login identity of its own. The nonce table is
-- writable ONLY by this role (and the privileged owner) — the app-request role can never touch it.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'safebox') then
        create role safebox nologin nosuperuser nobypassrls;
    end if;
end $$;

grant usage on schema public to safebox;

-- The broker claims each capability nonce exactly once (single-use → no replay). One row per
-- consumed nonce; `expires_at` is the capability's epoch-seconds expiry so a reconciliation sweep
-- can prune long-dead rows. Net-new table; REPLACE-guard not needed (no prior shape exists).
create table if not exists safebox_used_nonces (
    nonce      text primary key,
    expires_at bigint not null,
    claimed_at timestamptz not null default now()
);

-- Writable only by the safebox role (the owner/superuser keeps full authority implicitly). The
-- app-request role (`takyon_app`) is deliberately NOT granted anything here — the broker's replay
-- defense substrate is invisible to the per-customer app plane.
revoke all on table safebox_used_nonces from public;
grant select, insert, delete on table safebox_used_nonces to safebox;

-- Prune consumed-nonce rows whose capability has already expired (epoch seconds). Returns the
-- number of rows removed. A best-effort housekeeping sweep — replay safety never depends on it
-- (an unexpired nonce is always still present), it only bounds the table's growth.
create or replace function safebox_prune_used_nonces(p_now bigint)
returns bigint
language sql
security definer
set search_path = public, pg_temp
as $$
    with deleted as (
        delete from safebox_used_nonces where expires_at <= p_now returning 1
    )
    select count(*)::bigint from deleted;
$$;

grant execute on function safebox_prune_used_nonces(bigint) to safebox;

-- ── usage-gate SECURITY DEFINER functions ───────────────────────────────────────────────
-- The composite each gate function returns: a `refusal` discriminator (NULL on success) plus the
-- figure columns the typed Python exceptions need, then the full app_usage_events row (in the exact
-- _EVENT_COLUMNS order app_usage._event_from_row expects). On success refusal is NULL and the event
-- columns are populated; on refusal only refusal + the relevant figures are set and NO row is
-- written. This lets app_usage.py re-raise the identical typed exception with identical fields,
-- never parsing an error string.
drop type if exists safebox_usage_gate_result cascade;
create type safebox_usage_gate_result as (
    refusal                 text,
    -- TRUE when the call was an idempotent no-op on an already-finalized row (settle/release after
    -- finalization). app_usage.settle_usage uses this to skip the OpenMeter mirror exactly as the
    -- former in-Python `return event` short-circuit did (it returned BEFORE the mirror call).
    is_noop                 boolean,
    -- figures for the typed refusals
    fig_status              text,    -- AppBudgetInactive.status
    fig_hard_limit_microusd bigint,  -- AppBudgetExceeded.hard_limit_microusd
    fig_user_limit_microusd bigint,  -- AppUserBudgetExceeded.user_monthly_limit_microusd
    fig_committed_microusd  bigint,  -- *Exceeded.committed_microusd
    fig_requested_microusd  bigint,  -- *Exceeded.requested_microusd
    -- the event row, in _EVENT_COLUMNS order
    id                      uuid,
    business_slug           text,
    app_user_id             uuid,
    app_user_tier           text,
    reservation_key         text,
    purpose                 text,
    route                   text,
    status                  text,
    estimated_cost_microusd bigint,
    actual_cost_microusd    bigint,
    input_tokens            integer,
    output_tokens           integer,
    provider_request_id     text,
    provider                text,
    model                   text,
    error                   text,
    metadata                jsonb,
    created_at              timestamptz,
    completed_at            timestamptz
);

-- safebox_reserve_usage — verbatim port of app_usage.reserve_usage's row ops (the with-transaction
-- body). Input validation stays in Python; this is the atomic-under-the-budget-row-lock gate.
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
begin
    -- _ensure_budget_locked: open the budget with NO pool cap if absent, lock its row, roll the
    -- weekly window forward if elapsed. Same statements as the Python helper.
    insert into app_budgets (business_slug) values (p_business_slug)
        on conflict (business_slug) do nothing;
    select status, hard_limit_microusd, current_period_start
        into v_status, v_hard_limit, v_period_start
        from app_budgets where business_slug = p_business_slug for update;
    -- The roll writes into SEPARATE variables: an UPDATE ... RETURNING ... INTO sets its targets to
    -- NULL when zero rows are returned, so we must not clobber the locked SELECT above. Adopt the
    -- rolled values only when a row was actually returned (FOUND) — mirroring the Python
    -- `rolled if rolled is not None else row`.
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

    -- Idempotent on reservation_key: a replay returns the SAME reserved row without holding twice.
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
        return r;  -- refusal stays NULL → existing row
    end if;

    -- _require_app_user: the sub-user must belong to THIS business.
    if p_app_user_id is not null then
        select exists (
            select 1 from app_users where business_slug = p_business_slug and id = p_app_user_id
        ) into v_user_exists;
        if not v_user_exists then
            r.refusal := 'app_user_not_found';
            return r;
        end if;
    end if;

    -- Per-subuser gate.
    if p_app_user_id is not null and p_user_monthly_limit_microusd is not null then
        select coalesce(sum(case
                when status = 'reserved' then estimated_cost_microusd
                when status = 'completed' then actual_cost_microusd
                else 0 end), 0)
            into v_user_committed
            from app_usage_events
            where business_slug = p_business_slug and app_user_id = p_app_user_id
              and created_at >= v_period_start;
        if v_user_committed + p_estimated_cost_microusd > p_user_monthly_limit_microusd then
            r.refusal := 'app_user_budget_exceeded';
            r.fig_user_limit_microusd := p_user_monthly_limit_microusd;
            r.fig_committed_microusd := v_user_committed;
            r.fig_requested_microusd := p_estimated_cost_microusd;
            return r;
        end if;
    end if;

    -- Per-business pool gate: ONLY when an explicit cap is set (NULL sentinel = no pool cap).
    if v_hard_limit is not null then
        select coalesce(sum(case
                when status = 'reserved' then estimated_cost_microusd
                when status = 'completed' then actual_cost_microusd
                else 0 end), 0)
            into v_committed
            from app_usage_events
            where business_slug = p_business_slug and created_at >= v_period_start;
        if v_committed + p_estimated_cost_microusd > v_hard_limit then
            r.refusal := 'budget_exceeded';
            r.fig_hard_limit_microusd := v_hard_limit;
            r.fig_committed_microusd := v_committed;
            r.fig_requested_microusd := p_estimated_cost_microusd;
            return r;
        end if;
    end if;

    insert into app_usage_events
        (business_slug, app_user_id, app_user_tier, reservation_key, purpose, route,
         status, estimated_cost_microusd, provider, model, metadata)
        values (p_business_slug, p_app_user_id, p_app_user_tier, p_reservation_key, p_purpose,
                p_route, 'reserved', p_estimated_cost_microusd, p_provider, p_model, p_metadata)
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

-- safebox_settle_usage — verbatim port of app_usage.settle_usage's row ops. Locks the event row,
-- no-op (returns existing) if already finalized, else reserved → completed recording the actual.
-- NEVER re-checks the cap (money-truth). COALESCE-preserve + metadata-merge are unchanged.
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
begin
    select status into v_status
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
        return r;  -- already finalized → no-op, refusal NULL
    end if;
    update app_usage_events set
        status = 'completed',
        actual_cost_microusd = p_actual_cost_microusd,
        input_tokens = coalesce(p_input_tokens, input_tokens),
        output_tokens = coalesce(p_output_tokens, output_tokens),
        provider_request_id = coalesce(p_provider_request_id, provider_request_id),
        provider = coalesce(p_provider, provider),
        model = coalesce(p_model, model),
        metadata = metadata || coalesce(p_metadata, '{}'::jsonb),
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

-- safebox_release_usage — verbatim port of app_usage.release_usage's row ops. Locks the event row,
-- no-op if finalized, else reserved → failed (error given) | released (clean cancel), actual = 0.
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
begin
    select status into v_status
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
        return r;  -- already finalized → no-op
    end if;
    v_new_status := case when p_error is not null then 'failed' else 'released' end;
    update app_usage_events set
        status = v_new_status,
        actual_cost_microusd = 0,
        error = coalesce(p_error, error),
        metadata = metadata || coalesce(p_metadata, '{}'::jsonb),
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

-- ── reconciliation for held rows ─────────────────────────────────────────────────────────
-- A held `reserved` row whose provider call neither settled nor released (e.g. the runtime crashed
-- mid-call) holds its estimate against committed spend forever. This sweeper releases every
-- `reserved` row older than p_older_than_seconds back to 'released' (actual = 0, no spend recorded)
-- — the same terminal state release_usage with no error produces — so the held estimate is freed and
-- the weekly allowance is not pinned by an orphaned hold. SECURITY DEFINER so the runtime can run it
-- without direct UPDATE on the ledger. Returns the number of rows reconciled. Conservative: only
-- touches `reserved` rows past the age cutoff; never reopens a finalized row.
create or replace function safebox_reconcile_held_usage(p_older_than_seconds bigint)
returns bigint
language sql
security definer
set search_path = public, pg_temp
as $$
    with reconciled as (
        update app_usage_events set
            status = 'released',
            actual_cost_microusd = 0,
            error = coalesce(error, 'reconciled_orphaned_hold'),
            completed_at = now(),
            updated_at = now()
        where status = 'reserved'
          and created_at <= now() - make_interval(secs => p_older_than_seconds)
        returning 1
    )
    select count(*)::bigint from reconciled;
$$;

-- ── grants + revoke (the boundary) ───────────────────────────────────────────────────────
-- The runtime opens the usage writes under its privileged owner connection (core._leaf_conn), which
-- owns these SECURITY DEFINER functions and may execute them implicitly. Grant EXECUTE to the
-- restricted app-request role too, so the gate remains callable from any runtime scope while the
-- raw table write is closed off below.
grant execute on function safebox_reserve_usage(
    text, bigint, text, uuid, bigint, text, text, text, text, text, jsonb) to takyon_app;
grant execute on function safebox_settle_usage(
    text, text, bigint, integer, integer, text, text, text, jsonb) to takyon_app;
grant execute on function safebox_release_usage(text, text, text, jsonb) to takyon_app;
grant execute on function safebox_reconcile_held_usage(bigint) to takyon_app;

-- Close the direct-write hole: 0030 granted takyon_app INSERT/UPDATE/DELETE on app_usage_events,
-- but the gate functions above are now the ONLY sanctioned writer. Revoke direct write privilege so
-- a stray or forged write under the app role is DENIED at the DB. SELECT is retained (the 0027 RLS
-- read policy + the per-customer read path still need it).
revoke insert, update, delete on app_usage_events from takyon_app;
