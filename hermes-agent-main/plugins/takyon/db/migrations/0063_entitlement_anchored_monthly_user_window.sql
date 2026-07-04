-- 0063_entitlement_anchored_monthly_user_window.sql
-- Per-subuser usage window: business ISO-week → ENTITLEMENT-ANCHORED MONTH (subuser-billing-plan WS1).
--
-- Before this migration the per-subuser gate aggregated committed spend over the BUSINESS budget
-- window (ISO week, migration 0035) and callers pro-rated the plan's MONTHLY
-- included_ai_budget_microusd onto that week (× 7/30, ai_gateway._user_weekly_budget_microusd).
-- Totals were ~right, but a customer could never burst past ~23% of their monthly allowance in
-- any one week, and the window reset on a global Monday, not their billing anniversary.
--
-- After: the gate derives the customer's own window INSIDE the SECURITY DEFINER function — the
-- active paid entitlement's current_period_end minus one month (plans are monthly-only, enforced
-- fail-loud at the plan choke point), so the window follows the customer's billing anniversary and
-- callers pass the FULL monthly allowance. Window and allowance move together in one deploy: the
-- paired code change retires the ×7/30 pro-rate in the same commit.
--
-- Why the derivation lives in the gate and not the callers: the limit is caller-supplied, so a
-- caller-supplied window would let one missed call site pair a monthly limit with a weekly window
-- (the exact 4.3× overspend migration 0035 fixed). With the gate owning the window, a stale caller
-- that still passes the pro-rated weekly figure UNDERspends (safe direction), never overspends.
--
-- Fallbacks (all conservative — shorter window, so less spendable, never more):
--   * no active paid entitlement row → the business budget window (unchanged behavior);
--   * entitlement period already elapsed (current_period_end <= now(), e.g. dunning smart-retry)
--     → the business budget window, so an unpaid period never mints a fresh allowance;
--   * implausible period (current_period_end more than one month out — impossible for the
--     monthly-only plans the choke point admits) → the business budget window.
--
-- The entitlement pick below is VERBATIM the ranking takyon_app_action_usage_limit (migration
-- 0047) uses to resolve the plan for billable actions, so the gate and the limit resolvers agree
-- on which entitlement funds the customer: status in (active, trialing), a paid tier, not the
-- openmeter mirror, ranked owner → paid/pro → rest, newest first.
--
-- The business-pool gate (hard_limit_microusd sentinel, invariant 9) keeps the business ISO-week
-- window — it is an explicit operator ceiling on the pool, not the customer allowance.
--
-- Same function signature as migration 0037, so this is a plain CREATE OR REPLACE; the numeric
-- replay order (0037 then 0063) keeps re-runs idempotent.

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

    -- Per-subuser gate over the customer's OWN entitlement-anchored monthly window (0063).
    if p_app_user_id is not null and p_user_monthly_limit_microusd is not null then
        v_user_period_start := v_period_start;  -- conservative fallback: the business window
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
            r.refusal := 'app_user_budget_exceeded';
            r.fig_user_limit_microusd := p_user_monthly_limit_microusd;
            r.fig_committed_microusd := v_user_committed;
            r.fig_requested_microusd := p_estimated_cost_microusd;
            return r;
        end if;
    end if;

    -- Per-business pool gate: ONLY when an explicit cap is set (NULL sentinel = no pool cap).
    -- Stays on the business ISO-week window — an operator ceiling on the pool, not the allowance.
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
