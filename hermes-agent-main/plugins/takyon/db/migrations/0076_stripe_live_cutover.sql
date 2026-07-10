-- 0076_stripe_live_cutover.sql
-- Schema only. Applying/replaying migrations never changes Stripe environment data.
-- The one-time production retirement is an explicit root-SSH operation through
-- takyon_finalize_stripe_live_cutover().

alter type business_creative_credit_entry_kind add value if not exists 'retire';

begin;

create table if not exists stripe_environment_cutovers (
    cutover_key       text primary key,
    source_account_id text not null,
    target_account_id text not null,
    ssh_client        inet not null,
    operator_host     text not null,
    applied_at        timestamptz not null default now(),
    check (source_account_id ~ '^acct_[A-Za-z0-9]+$'),
    check (target_account_id ~ '^acct_[A-Za-z0-9]+$'),
    check (source_account_id <> target_account_id)
);

create table if not exists stripe_sandbox_user_handles_archive (
    cutover_key                          text not null references stripe_environment_cutovers(cutover_key),
    user_id                              uuid not null references users(id) on delete restrict,
    stripe_connect_account_id            text,
    stripe_connect_status                text not null,
    operator_billing_customer_id         text,
    operator_billing_subscription_id     text,
    operator_billing_subscription_status text not null,
    retired_at                           timestamptz not null default now(),
    primary key (cutover_key, user_id)
);

create table if not exists stripe_sandbox_app_user_credit_grants_archive (
    cutover_key               text not null references stripe_environment_cutovers(cutover_key),
    grant_id                  uuid not null,
    business_slug             text not null,
    app_user_id               uuid not null,
    amount_microusd           bigint not null,
    remaining_before_microusd bigint not null,
    source                    text not null,
    source_id                 text not null,
    grant_created_at          timestamptz not null,
    grant_updated_at          timestamptz not null,
    retired_at                timestamptz not null default now(),
    primary key (cutover_key, grant_id)
);

revoke all on table stripe_environment_cutovers from public;
revoke all on table stripe_sandbox_user_handles_archive from public;
revoke all on table stripe_sandbox_app_user_credit_grants_archive from public;
revoke all on table stripe_environment_cutovers from
    takyon_operator_runtime, takyon_app_runtime, takyon_safebox_authority,
    takyon_runtime, takyon_app, safebox;
revoke all on table stripe_sandbox_user_handles_archive from
    takyon_operator_runtime, takyon_app_runtime, takyon_safebox_authority,
    takyon_runtime, takyon_app, safebox;
revoke all on table stripe_sandbox_app_user_credit_grants_archive from
    takyon_operator_runtime, takyon_app_runtime, takyon_safebox_authority,
    takyon_runtime, takyon_app, safebox;

-- The migration login is the only caller of the explicit finalizer. Some databases predate the
-- migration-owned-object topology, so grant that login an explicit RLS path to retire these rows.
drop policy if exists app_user_credit_grants_migration_cutover on app_user_credit_grants;
create policy app_user_credit_grants_migration_cutover on app_user_credit_grants
    for all to takyon_migration
    using (current_user = 'takyon_migration')
    with check (current_user = 'takyon_migration');

-- Atomic, single-use Checkout intent claim. Direct table mutation stays unavailable to callers.
-- Live callers must name the Stripe account resolved by the Safebox; the claim stays closed until
-- the one-shot cutover row durably binds production to that exact account. Dev/test callers pass
-- NULL and remain isolated in Stripe's test universe.
drop function if exists takyon_safebox_claim_app_checkout_intent(
    uuid, text, text, text, text
);
create or replace function takyon_safebox_claim_app_checkout_intent(
    p_intent_id uuid,
    p_business_slug text,
    p_plan_key text,
    p_customer_email text,
    p_client_reference_id text,
    p_live_target_account_id text
)
returns table (
    intent_id uuid,
    app_user_id uuid,
    customer_email text,
    client_reference_id text,
    price_cents integer,
    currency text,
    billing_interval text,
    tier text,
    included_ai_budget_microusd bigint,
    included_action_quota integer,
    plan_metadata jsonb,
    business_mode text
)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    if session_user <> 'takyon_safebox_authority' then
        raise exception 'safebox_session_required' using errcode = '42501';
    end if;
    if p_live_target_account_id is not null then
        if p_live_target_account_id !~ '^acct_[A-Za-z0-9]+$' then
            raise exception 'stripe_live_target_account_mismatch' using errcode = '22023';
        end if;
        if not exists (
            select 1
              from stripe_environment_cutovers c
             where c.cutover_key = 'sandbox-to-live-v1'
               and c.target_account_id = p_live_target_account_id
        ) then
            raise exception 'stripe_live_cutover_required' using errcode = '55000';
        end if;
    end if;
    perform pg_advisory_xact_lock(
        hashtextextended('takyon-plan-economics:' || p_business_slug || ':' || p_plan_key, 0)
    );
    return query
    update app_checkout_intents i
       set status = 'stripe_creating', updated_at = now()
      from app_plan_policies p, app_users u, businesses b
     where i.id = p_intent_id
       and i.business_slug = p_business_slug
       and i.plan_key = p_plan_key
       and i.status in ('created', 'stripe_creating')
       and i.created_at > now() - interval '23 hours'
       and i.app_user_id is not null
       and p.business_slug = i.business_slug
       and p.plan_key = i.plan_key
       and p.billing_interval = 'month'
       and p.price_cents > 0
       and lower(p.currency) = 'usd'
       and p.saleable
       and lower(coalesce(p.metadata->>'status', p.metadata->>'lifecycle_status', ''))
           not in ('archived', 'deprecated', 'disabled', 'inactive', 'retired')
       and u.business_slug = i.business_slug
       and u.id = i.app_user_id
       and u.status = 'active'
       and b.slug = i.business_slug
       and b.status = 'active'
       and b.mode = 'live'
       and (coalesce(p_customer_email, '') = '' or i.customer_email is null
            or lower(i.customer_email) = lower(p_customer_email))
       and (coalesce(p_client_reference_id, '') = ''
            or i.client_reference_id = p_client_reference_id)
    returning i.id, i.app_user_id, i.customer_email, i.client_reference_id,
              p.price_cents, lower(p.currency), p.billing_interval, p.tier,
              p.included_ai_budget_microusd, p.included_action_quota, p.metadata, b.mode;
end;
$$;

create or replace function takyon_safebox_release_app_checkout_intent(p_intent_id uuid)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_released bigint;
begin
    if session_user <> 'takyon_safebox_authority' then
        raise exception 'safebox_session_required' using errcode = '42501';
    end if;
    update app_checkout_intents
       set status = 'created', updated_at = now()
     where id = p_intent_id and status = 'stripe_creating';
    get diagnostics v_released = row_count;
    return v_released = 1;
end;
$$;

revoke execute on function takyon_safebox_claim_app_checkout_intent(
    uuid, text, text, text, text, text
) from public, takyon_operator_runtime, takyon_app_runtime, takyon_runtime, takyon_app, safebox;
revoke execute on function takyon_safebox_release_app_checkout_intent(uuid)
    from public, takyon_operator_runtime, takyon_app_runtime, takyon_runtime, takyon_app, safebox;
grant execute on function takyon_safebox_claim_app_checkout_intent(
    uuid, text, text, text, text, text
) to takyon_safebox_authority;
grant execute on function takyon_safebox_release_app_checkout_intent(uuid)
    to takyon_safebox_authority;

-- Refund/dispute custody clawback. Debit whatever is still owed immediately, persist any
-- shortfall, recover that shortfall from later accruals, and block payouts until it is cleared.
do $$
begin
    if not exists (
        select 1 from pg_type t join pg_namespace n on n.oid = t.typnamespace
         where n.nspname = 'public' and t.typname = 'safebox_custody_clawback_result'
    ) then
        create type safebox_custody_clawback_result as (
            refusal text,
            applied_cents bigint,
            shortfall_cents bigint,
            new_owed bigint,
            replayed boolean
        );
    end if;
end $$;

create or replace function safebox_custody_clawback(
    p_user_id uuid,
    p_business_slug text,
    p_amount_cents bigint,
    p_idempotency_key text,
    p_stripe_ref text,
    p_metadata jsonb
)
returns safebox_custody_clawback_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r safebox_custody_clawback_result;
    v_owed bigint;
    v_existing custody_entries%rowtype;
begin
    if session_user not in ('takyon_safebox_authority', 'takyon_migration') then
        raise exception 'safebox_session_required' using errcode = '42501';
    end if;
    if p_amount_cents <= 0 or coalesce(trim(p_idempotency_key), '') = '' then
        raise exception 'invalid_custody_clawback' using errcode = '22023';
    end if;
    select owed_balance_cents into v_owed
      from custody_accounts where user_id = p_user_id for update;
    if not found then
        r.refusal := 'no_custody_account';
        return r;
    end if;
    select * into v_existing from custody_entries
     where idempotency_key = p_idempotency_key;
    if found then
        if v_existing.kind <> 'adjustment'
           or coalesce((v_existing.metadata->>'clawback_requested_cents')::bigint, -1)
              <> p_amount_cents then
            raise exception 'custody_clawback_idempotency_mismatch' using errcode = '22023';
        end if;
        r.applied_cents := greatest(0, -v_existing.net_cents);
        r.shortfall_cents := coalesce(
            (v_existing.metadata->>'clawback_shortfall_cents')::bigint, 0
        );
        r.new_owed := v_owed;
        r.replayed := true;
        return r;
    end if;
    r.applied_cents := least(v_owed, p_amount_cents);
    r.shortfall_cents := p_amount_cents - r.applied_cents;
    r.new_owed := v_owed - r.applied_cents;
    r.replayed := false;
    update custody_accounts
       set owed_balance_cents = r.new_owed, updated_at = now()
     where user_id = p_user_id;
    insert into custody_entries (
        user_id, business_slug, kind, gross_cents, fee_cents, net_cents,
        stripe_ref, idempotency_key, metadata
    ) values (
        p_user_id, p_business_slug, 'adjustment', p_amount_cents, 0,
        -r.applied_cents, p_stripe_ref, p_idempotency_key,
        coalesce(p_metadata, '{}'::jsonb) || jsonb_build_object(
            'custody_clawback', true,
            'clawback_requested_cents', p_amount_cents,
            'clawback_applied_cents', r.applied_cents,
            'clawback_shortfall_cents', r.shortfall_cents
        )
    );
    return r;
end;
$$;

create or replace function safebox_custody_accrue(
    p_user_id uuid,
    p_business_slug text,
    p_gross_cents bigint,
    p_fee_cents bigint,
    p_net_cents bigint,
    p_idempotency_key text,
    p_stripe_ref text,
    p_metadata jsonb
)
returns safebox_custody_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r safebox_custody_result;
    v_owed bigint;
    v_exists boolean;
    v_pending bigint;
    v_recovery bigint;
begin
    select owed_balance_cents into v_owed
      from custody_accounts where user_id = p_user_id for update;
    if not found then
        r.refusal := 'no_custody_account';
        return r;
    end if;
    select exists (
        select 1 from custody_entries where idempotency_key = p_idempotency_key
    ) into v_exists;
    if v_exists then
        r.new_owed := v_owed;
        return r;
    end if;
    select greatest(
        0,
        coalesce(sum(
            case when kind = 'adjustment'
                       and metadata->>'custody_clawback' = 'true'
                 then coalesce((metadata->>'clawback_shortfall_cents')::bigint, 0)
                 else 0 end
        ), 0)
        - coalesce(sum(
            case when kind = 'accrual'
                 then coalesce((metadata->>'clawback_recovery_cents')::bigint, 0)
                 else 0 end
        ), 0)
    ) into v_pending
      from custody_entries where user_id = p_user_id;
    v_recovery := least(greatest(p_net_cents, 0), v_pending);
    r.new_owed := v_owed + p_net_cents - v_recovery;
    update custody_accounts set owed_balance_cents = r.new_owed, updated_at = now()
     where user_id = p_user_id;
    insert into custody_entries (
        user_id, business_slug, kind, gross_cents, fee_cents, net_cents,
        stripe_ref, idempotency_key, metadata
    ) values (
        p_user_id, p_business_slug, 'accrual', p_gross_cents, p_fee_cents,
        p_net_cents - v_recovery, p_stripe_ref, p_idempotency_key,
        coalesce(p_metadata, '{}'::jsonb)
        || case when v_recovery > 0
                then jsonb_build_object('clawback_recovery_cents', v_recovery)
                else '{}'::jsonb end
    );
    return r;
end;
$$;

create or replace function safebox_custody_payout(
    p_user_id uuid,
    p_amount_cents bigint,
    p_idempotency_key text,
    p_stripe_ref text
)
returns safebox_custody_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r safebox_custody_result;
    v_owed bigint;
    v_paid_out bigint;
    v_exists boolean;
    v_pending bigint;
begin
    select owed_balance_cents, paid_out_cents into v_owed, v_paid_out
      from custody_accounts where user_id = p_user_id for update;
    if not found then
        r.refusal := 'no_custody_account';
        return r;
    end if;
    select exists (
        select 1 from custody_entries where idempotency_key = p_idempotency_key
    ) into v_exists;
    if v_exists then
        r.new_owed := v_owed;
        return r;
    end if;
    select greatest(
        0,
        coalesce(sum(
            case when kind = 'adjustment'
                       and metadata->>'custody_clawback' = 'true'
                 then coalesce((metadata->>'clawback_shortfall_cents')::bigint, 0)
                 else 0 end
        ), 0)
        - coalesce(sum(
            case when kind = 'accrual'
                 then coalesce((metadata->>'clawback_recovery_cents')::bigint, 0)
                 else 0 end
        ), 0)
    ) into v_pending
      from custody_entries where user_id = p_user_id;
    if v_pending > 0 then
        r.refusal := 'custody_clawback_pending';
        r.fig_requested_cents := p_amount_cents;
        r.fig_owed_cents := v_pending;
        r.new_owed := v_owed;
        return r;
    end if;
    if p_amount_cents > v_owed then
        r.refusal := 'insufficient_custody';
        r.fig_requested_cents := p_amount_cents;
        r.fig_owed_cents := v_owed;
        return r;
    end if;
    r.new_owed := v_owed - p_amount_cents;
    update custody_accounts
       set owed_balance_cents = r.new_owed,
           paid_out_cents = v_paid_out + p_amount_cents,
           updated_at = now()
     where user_id = p_user_id;
    insert into custody_entries (
        user_id, kind, gross_cents, fee_cents, net_cents, stripe_ref, idempotency_key
    ) values (
        p_user_id, 'payout', p_amount_cents, 0, -p_amount_cents,
        p_stripe_ref, p_idempotency_key
    );
    return r;
end;
$$;

alter function safebox_custody_clawback(uuid, text, bigint, text, text, jsonb)
    owner to takyon_migration;
alter function safebox_custody_accrue(uuid, text, bigint, bigint, bigint, text, text, jsonb)
    owner to takyon_migration;
alter function safebox_custody_payout(uuid, bigint, text, text)
    owner to takyon_migration;
revoke execute on function safebox_custody_clawback(uuid, text, bigint, text, text, jsonb)
    from public, takyon_runtime, takyon_operator_runtime, takyon_app_runtime, takyon_app, safebox;
revoke execute on function safebox_custody_accrue(uuid, text, bigint, bigint, bigint, text, text, jsonb)
    from public, takyon_runtime, takyon_operator_runtime, takyon_app_runtime, takyon_app, safebox;
revoke execute on function safebox_custody_payout(uuid, bigint, text, text)
    from public, takyon_runtime, takyon_operator_runtime, takyon_app_runtime, takyon_app, safebox;
grant execute on function safebox_custody_clawback(uuid, text, bigint, text, text, jsonb)
    to takyon_safebox_authority, takyon_migration;
grant execute on function safebox_custody_accrue(uuid, text, bigint, bigint, bigint, text, text, jsonb)
    to takyon_safebox_authority, takyon_migration;
grant execute on function safebox_custody_payout(uuid, bigint, text, text)
    to takyon_safebox_authority, takyon_migration;

-- Explicit one-shot data transition. The root-SSH wrapper supplies the audited host/client context
-- and connects only as takyon_migration. A durable singleton prevents any later migration replay or
-- invocation from touching post-cutover live rows.
create or replace function takyon_finalize_stripe_live_cutover(
    p_source_account_id text,
    p_target_account_id text,
    p_ssh_client inet,
    p_operator_host text
)
returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    v_key constant text := 'sandbox-to-live-v1';
    v_inserted text;
    v_plans bigint := 0;
    v_entitlements bigint := 0;
    v_intents bigint := 0;
    v_sessions bigint := 0;
    v_revenue bigint := 0;
    v_users bigint := 0;
    v_custody bigint := 0;
    v_credits bigint := 0;
    v_app_user_credit_grants bigint := 0;
    v_app_user_credit_grants_archived bigint := 0;
    v_allowances bigint := 0;
    v_creative_releases bigint := 0;
    v_allowance_refunds bigint := 0;
    v_reservation text;
    v_allowance_period_start timestamptz;
begin
    if current_user <> 'takyon_migration' then
        raise exception 'migration_role_required' using errcode = '42501';
    end if;
    if coalesce(p_source_account_id, '') !~ '^acct_[A-Za-z0-9]+$'
       or coalesce(p_target_account_id, '') !~ '^acct_[A-Za-z0-9]+$'
       or p_source_account_id = p_target_account_id
       or p_ssh_client is null
       or coalesce(trim(p_operator_host), '') = '' then
        raise exception 'complete_cutover_context_required' using errcode = '22023';
    end if;

    perform pg_advisory_xact_lock(hashtext('takyon-stripe-live-cutover'));
    insert into stripe_environment_cutovers (
        cutover_key, source_account_id, target_account_id, ssh_client, operator_host
    ) values (
        v_key, p_source_account_id, p_target_account_id, p_ssh_client, trim(p_operator_host)
    ) on conflict (cutover_key) do nothing
    returning cutover_key into v_inserted;
    if v_inserted is null then
        return jsonb_build_object('applied', false, 'reason', 'already_applied');
    end if;

    if exists (select 1 from app_entitlements where metadata->>'stripe_environment' = 'live')
       or exists (select 1 from app_checkout_sessions where metadata->>'stripe_environment' = 'live')
       or exists (select 1 from app_revenue_events where metadata->>'stripe_environment' = 'live')
       or exists (
           select 1 from app_plan_policies
            where saleable and price_cents > 0 and lower(currency) <> 'usd'
       )
       or exists (
           select 1 from app_entitlements
            where status in ('active', 'trialing')
              and source not in ('stripe', 'manual_test', 'operator_ssh', 'openmeter')
       )
       or exists (
           select 1 from custody_entries
            where kind = 'accrual'
              and coalesce(stripe_ref, '') not like 'cs_test_%'
       )
       or exists (
           select 1 from custody_entries
            where kind not in ('accrual', 'payout') and net_cents <> 0
       )
       or exists (
           select 1 from custody_accounts a
            where a.owed_balance_cents <> (
                select coalesce(sum(e.net_cents), 0)
                  from custody_entries e where e.user_id = a.user_id
            )
       ) then
        raise exception 'live_stripe_rows_block_sandbox_retirement' using errcode = '55000';
    end if;

    -- The runbook drains workers first. Canonically finalize any stale sandbox holds so a delayed
    -- commit/settle becomes an idempotent no-op instead of resurrecting retired value.
    for v_reservation in
        select r.reservation_key
         from business_creative_credit_entries r
         where r.kind = 'reserve'
           and r.reservation_key is not null
           and not exists (
               select 1 from business_creative_credit_entries f
                where f.reservation_key = r.reservation_key and f.kind in ('commit', 'release')
           )
           and exists (
               select 1 from business_creative_credit_entries g
                where g.business_slug = r.business_slug
                  and g.kind = 'grant' and g.stripe_ref is not null
           )
    loop
        perform safebox_credits_release(
            v_reservation,
            jsonb_build_object('stripe_environment', 'test', 'reason', 'sandbox_to_live_cutover')
        );
        v_creative_releases := v_creative_releases + 1;
    end loop;

    for v_reservation in
        with latest_grants as (
            select distinct on (e.user_id) e.user_id, e.idempotency_key
              from billing_entries e where e.bucket = 'allowance' and e.kind = 'grant'
             order by e.user_id, e.created_at desc, e.id desc
        )
        select distinct r.reservation_key
          from billing_entries r join latest_grants g using (user_id)
         where (g.idempotency_key like 'operator-subscription:%'
                or g.idempotency_key like 'manual-test-reup-%')
           and r.kind = 'reserve'
           and not exists (
               select 1 from billing_entries f
                where f.reservation_key = r.reservation_key and f.kind in ('settle', 'refund')
           )
    loop
        perform safebox_billing_refund(v_reservation);
        v_allowance_refunds := v_allowance_refunds + 1;
    end loop;

    update app_plan_policies
       set metadata = metadata || jsonb_build_object(
               'stripe_sandbox_catalog', jsonb_build_object(
                   'product_id', stripe_product_id, 'price_id', stripe_price_id,
                   'retired_at', now(), 'cutover_key', v_key
               )
           ),
           stripe_product_id = null, stripe_price_id = null, updated_at = now()
     where stripe_product_id is not null or stripe_price_id is not null;
    get diagnostics v_plans = row_count;

    update app_entitlements
       set status = 'sandbox_retired',
           metadata = metadata || jsonb_build_object(
               'stripe_environment', 'test', 'sandbox_status_before_cutover', status,
               'sandbox_retired_at', now(), 'stripe_cutover_key', v_key
           ), updated_at = now()
     where source in ('stripe', 'manual_test');
    get diagnostics v_entitlements = row_count;

    with desired as (
        select u.business_slug, u.id,
               coalesce((
                   select e.tier from app_entitlements e
                    where e.business_slug = u.business_slug and e.app_user_id = u.id
                      and e.status in ('active', 'trialing')
                      and lower(e.tier) not in ('', 'free', 'none', 'unentitled')
                      and e.source <> 'openmeter'
                    order by case lower(e.tier)
                               when 'owner' then 0 when 'paid' then 1 when 'pro' then 1 else 5
                             end, e.updated_at desc limit 1
               ), 'unentitled') as tier
          from app_users u
    )
    update app_users u set tier = d.tier, updated_at = now()
      from desired d
     where u.business_slug = d.business_slug and u.id = d.id and u.tier is distinct from d.tier;

    insert into stripe_sandbox_app_user_credit_grants_archive (
        cutover_key, grant_id, business_slug, app_user_id, amount_microusd,
        remaining_before_microusd, source, source_id, grant_created_at, grant_updated_at
    )
    select v_key, id, business_slug, app_user_id, amount_microusd, remaining_microusd,
           source, source_id, created_at, updated_at
      from app_user_credit_grants
     where source = 'stripe_payment';
    get diagnostics v_app_user_credit_grants_archived = row_count;

    update app_user_credit_grants
       set remaining_microusd = 0, updated_at = now()
     where source = 'stripe_payment' and remaining_microusd > 0;
    get diagnostics v_app_user_credit_grants = row_count;

    update app_checkout_intents
       set status = case when status in ('created', 'pending', 'stripe_creating')
                         then 'sandbox_retired' else status end,
           metadata = metadata || jsonb_build_object(
               'stripe_environment', 'test', 'sandbox_status_before_cutover', status,
               'sandbox_retired_at', now(), 'stripe_cutover_key', v_key
           ), updated_at = now();
    get diagnostics v_intents = row_count;

    update app_checkout_sessions
       set metadata = metadata || jsonb_build_object(
           'stripe_environment', 'test', 'sandbox_retired_at', now(), 'stripe_cutover_key', v_key
       ), updated_at = now();
    get diagnostics v_sessions = row_count;

    update app_revenue_events
       set status = case when status like 'test_%' then status else 'test_' || status end,
           metadata = metadata || jsonb_build_object(
               'stripe_environment', 'test', 'sandbox_status_before_cutover', status,
               'sandbox_retired_at', now(), 'stripe_cutover_key', v_key
           );
    get diagnostics v_revenue = row_count;

    insert into stripe_sandbox_user_handles_archive (
        cutover_key, user_id, stripe_connect_account_id, stripe_connect_status,
        operator_billing_customer_id, operator_billing_subscription_id,
        operator_billing_subscription_status
    )
    select v_key, id, stripe_connect_account_id, stripe_connect_status,
           operator_billing_customer_id, operator_billing_subscription_id,
           operator_billing_subscription_status
      from users
     where stripe_connect_account_id is not null
        or operator_billing_customer_id is not null
        or operator_billing_subscription_id is not null
        or operator_billing_subscription_status <> 'none';

    update users set
        stripe_connect_account_id = null, stripe_connect_status = 'none',
        operator_billing_customer_id = null, operator_billing_subscription_id = null,
        operator_billing_subscription_status = 'none'
     where stripe_connect_account_id is not null
        or operator_billing_customer_id is not null
        or operator_billing_subscription_id is not null
        or operator_billing_subscription_status <> 'none';
    get diagnostics v_users = row_count;

    with targets as (
        select user_id, owed_balance_cents from custody_accounts where owed_balance_cents > 0
    ), entries as (
        insert into custody_entries (
            user_id, kind, gross_cents, fee_cents, net_cents, idempotency_key, metadata
        )
        select user_id, 'adjustment', 0, 0, -owed_balance_cents,
               'stripe-sandbox-retire-0076:' || user_id::text,
               jsonb_build_object('stripe_environment', 'test', 'reason', 'sandbox_to_live_cutover')
          from targets returning user_id
    )
    update custody_accounts a set owed_balance_cents = 0, updated_at = now()
      where a.user_id in (select user_id from entries);
    get diagnostics v_custody = row_count;

    with stripe_grants as (
        select business_slug, sum(amount_credits)::bigint as granted
          from business_creative_credit_entries
         where kind = 'grant' and stripe_ref is not null group by business_slug
    ), targets as (
        select a.business_slug,
               -- Credits are fungible: historical commits do not prove whether Stripe-funded or
               -- bootstrap credits were consumed. Retire conservatively so no sandbox-funded
               -- value can survive the cutover as free live credit.
               least(a.balance_credits, g.granted)::bigint as retire,
               a.balance_credits
          from business_creative_credit_accounts a join stripe_grants g using (business_slug)
    ), entries as (
        insert into business_creative_credit_entries (
            business_slug, kind, amount_credits, balance_after_credits,
            idempotency_key, metadata
        )
        select business_slug, 'retire', retire, balance_credits - retire,
               'stripe-sandbox-retire-0076:' || business_slug,
               jsonb_build_object('stripe_environment', 'test', 'reason', 'sandbox_to_live_cutover')
          from targets where retire > 0 returning business_slug
    )
    update business_creative_credit_accounts a
       set balance_credits = t.balance_credits - t.retire, updated_at = now()
      from targets t
     where a.business_slug = t.business_slug and t.retire > 0;
    get diagnostics v_credits = row_count;

    v_allowance_period_start := clock_timestamp();
    with latest_grants as (
        select distinct on (e.user_id) e.user_id, e.idempotency_key
          from billing_entries e where e.bucket = 'allowance' and e.kind = 'grant'
         order by e.user_id, e.created_at desc, e.id desc
    ), targets as (
        select a.user_id from billing_accounts a join latest_grants g using (user_id)
         where g.idempotency_key like 'operator-subscription:%'
            or g.idempotency_key like 'manual-test-reup-%'
    ), entries as (
        insert into billing_entries (
            user_id, bucket, kind, amount_cents, balance_after_cents,
            idempotency_key, metadata
        )
        select user_id, 'allowance', 'grant', 0, 0,
               'stripe-sandbox-retire-0076:' || user_id::text,
               jsonb_build_object('stripe_environment', 'test', 'reason', 'sandbox_to_live_cutover')
          from targets returning user_id
    )
    update billing_accounts a set
        allowance_included_cents = 0, allowance_used_cents = 0,
        allowance_period_start = v_allowance_period_start,
        allowance_resets_at = null, updated_at = now()
      where a.user_id in (select user_id from entries);
    get diagnostics v_allowances = row_count;

    return jsonb_build_object(
        'applied', true, 'plans_cleared', v_plans,
        'entitlements_retired', v_entitlements, 'intents_retired', v_intents,
        'sessions_marked_test', v_sessions, 'revenue_marked_test', v_revenue,
        'user_handles_cleared', v_users, 'custody_accounts_zeroed', v_custody,
        'creative_credit_accounts_adjusted', v_credits,
        'app_user_credit_grants_zeroed', v_app_user_credit_grants,
        'app_user_credit_grants_archived', v_app_user_credit_grants_archived,
        'operator_allowances_cleared', v_allowances,
        'creative_reservations_released', v_creative_releases,
        'operator_allowance_reservations_refunded', v_allowance_refunds
    );
end;
$$;

revoke execute on function takyon_finalize_stripe_live_cutover(text, text, inet, text)
    from public, takyon_operator_runtime, takyon_app_runtime, takyon_safebox_authority,
         takyon_runtime, takyon_app, safebox;
grant execute on function takyon_finalize_stripe_live_cutover(text, text, inet, text)
    to takyon_migration;

-- Environment-aware revenue read. The runtime supplies its server-side Stripe mode; the product
-- caller cannot choose it.
create or replace function takyon_app_account_revenue_summary(
    p_business_slug text,
    p_session_hash text,
    p_stripe_environment text
)
returns table(amount_paid_cents bigint, count bigint)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_email text;
begin
    if p_stripe_environment not in ('test', 'live') then
        raise exception 'invalid_stripe_environment' using errcode = '22023';
    end if;
    select lower(coalesce(u.email, '')) into v_email
      from app_sessions s join app_users u
        on u.business_slug = s.business_slug and u.id = s.app_user_id
     where s.business_slug = p_business_slug and s.token_hash = p_session_hash
       and s.revoked_at is null and s.expires_at > now() and u.status = 'active'
     limit 1;
    if coalesce(v_email, '') = '' then
        return query select 0::bigint, 0::bigint;
        return;
    end if;
    return query
        select coalesce(sum(case when e.revenue_type = 'reversal'
                                 then -e.amount_paid_cents else e.amount_paid_cents end), 0)::bigint,
               count(*)::bigint
          from app_revenue_events e
         where e.business_slug = p_business_slug
           and lower(coalesce(e.customer_email, '')) = v_email
           and coalesce(e.metadata->>'stripe_environment', 'test') = p_stripe_environment;
end;
$$;

revoke execute on function takyon_app_account_revenue_summary(text, text) from
    public, takyon_app_runtime, takyon_app;
revoke execute on function takyon_app_account_revenue_summary(text, text, text) from public;
grant execute on function takyon_app_account_revenue_summary(text, text, text)
    to takyon_app_runtime, takyon_app;

commit;
