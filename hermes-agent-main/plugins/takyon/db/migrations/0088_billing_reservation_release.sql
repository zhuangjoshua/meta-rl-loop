-- 0088_billing_reservation_release.sql
--
-- Operator-compute failure cleanup releases an internal allowance reservation; it is not a
-- customer payment refund. Rename the ledger event and authority function accordingly, remove the
-- old callable function, and keep finalize authority exclusively on the Safebox role.

do $$
begin
    if exists (
        select 1
          from pg_enum e
          join pg_type t on t.oid = e.enumtypid
         where t.typname = 'billing_entry_kind' and e.enumlabel = 'refund'
    ) and not exists (
        select 1
          from pg_enum e
          join pg_type t on t.oid = e.enumtypid
         where t.typname = 'billing_entry_kind' and e.enumlabel = 'release'
    ) then
        alter type billing_entry_kind rename value 'refund' to 'release';
    elsif exists (
        select 1
          from pg_enum e
          join pg_type t on t.oid = e.enumtypid
         where t.typname = 'billing_entry_kind' and e.enumlabel = 'refund'
    ) then
        execute 'update billing_entries set kind = ''release'' where kind = ''refund''';
    end if;
end $$;

create or replace function safebox_billing_settle(
    p_reservation_key text,
    p_actual_cents     bigint
)
returns safebox_billing_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r            safebox_billing_result;
    v_user_id    uuid;
    v_a_resv     bigint;
    v_used       bigint;
    v_finalized  boolean;
    v_s_alloc    bigint;
    v_r_alloc    bigint;
    v_new_used   bigint;
begin
    select user_id, coalesce(sum(amount_cents) filter (where bucket = 'allowance'), 0)
      into v_user_id, v_a_resv
      from billing_entries
     where reservation_key = p_reservation_key and kind = 'reserve'
     group by user_id;
    if not found then
        r.refusal := 'unknown_reservation';
        return r;
    end if;
    select allowance_used_cents
      into v_used
      from billing_accounts
     where user_id = v_user_id
     for update;
    select exists (
        select 1
          from billing_entries
         where reservation_key = p_reservation_key and kind in ('settle', 'release')
    ) into v_finalized;
    if v_finalized then
        return r;
    end if;
    v_s_alloc := p_actual_cents;
    v_r_alloc := v_a_resv - v_s_alloc;
    v_new_used := v_used - v_r_alloc;
    update billing_accounts
       set allowance_used_cents = v_new_used, updated_at = now()
     where user_id = v_user_id;
    if v_s_alloc > 0 then
        insert into billing_entries
            (user_id, bucket, kind, amount_cents, balance_after_cents,
             reservation_key, idempotency_key)
        values
            (v_user_id, 'allowance', 'settle', v_s_alloc, v_used,
             p_reservation_key, p_reservation_key || ':settle:allowance');
    end if;
    if v_r_alloc > 0 then
        insert into billing_entries
            (user_id, bucket, kind, amount_cents, balance_after_cents,
             reservation_key, idempotency_key)
        values
            (v_user_id, 'allowance', 'release', v_r_alloc, v_new_used,
             p_reservation_key, p_reservation_key || ':release:allowance');
    end if;
    return r;
end;
$$;

create or replace function safebox_billing_release_reservation(
    p_reservation_key text
)
returns safebox_billing_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r            safebox_billing_result;
    v_user_id    uuid;
    v_a_resv     bigint;
    v_used       bigint;
    v_finalized  boolean;
    v_new_used   bigint;
begin
    select user_id, coalesce(sum(amount_cents) filter (where bucket = 'allowance'), 0)
      into v_user_id, v_a_resv
      from billing_entries
     where reservation_key = p_reservation_key and kind = 'reserve'
     group by user_id;
    if not found then
        r.refusal := 'unknown_reservation';
        return r;
    end if;
    select allowance_used_cents
      into v_used
      from billing_accounts
     where user_id = v_user_id
     for update;
    select exists (
        select 1
          from billing_entries
         where reservation_key = p_reservation_key and kind in ('settle', 'release')
    ) into v_finalized;
    if v_finalized then
        return r;
    end if;
    v_new_used := v_used - v_a_resv;
    update billing_accounts
       set allowance_used_cents = v_new_used, updated_at = now()
     where user_id = v_user_id;
    if v_a_resv > 0 then
        insert into billing_entries
            (user_id, bucket, kind, amount_cents, balance_after_cents,
             reservation_key, idempotency_key)
        values
            (v_user_id, 'allowance', 'release', v_a_resv, v_new_used,
             p_reservation_key, p_reservation_key || ':release:allowance');
    end if;
    return r;
end;
$$;

-- The one-shot Stripe cutover is a persistent function created by 0076. Migrate its exact four
-- internal-release references without duplicating the 300-line cutover body in a second source.
do $$
declare
    v_signature regprocedure := to_regprocedure(
        'takyon_finalize_stripe_live_cutover(text,text,inet,text)'
    );
    v_definition text;
begin
    if v_signature is not null then
        select pg_get_functiondef(v_signature) into v_definition;
        if position('safebox_billing_refund(v_reservation)' in v_definition) = 0 then
            if position('safebox_billing_release_reservation(v_reservation)' in v_definition) = 0 then
                raise exception 'unexpected Stripe cutover billing finalizer';
            end if;
        else
            v_definition := replace(
                v_definition,
                'safebox_billing_refund(v_reservation)',
                'safebox_billing_release_reservation(v_reservation)'
            );
            v_definition := replace(
                v_definition,
                'kind IN (''settle'', ''refund'')',
                'kind IN (''settle'', ''release'')'
            );
            v_definition := replace(
                v_definition,
                'kind in (''settle'', ''refund'')',
                'kind in (''settle'', ''release'')'
            );
            v_definition := replace(v_definition, 'v_allowance_refunds', 'v_allowance_releases');
            v_definition := replace(
                v_definition,
                'operator_allowance_reservations_refunded',
                'operator_allowance_reservations_released'
            );
            execute v_definition;
            if position('refund' in lower(pg_get_functiondef(v_signature))) > 0 then
                raise exception 'Stripe cutover still contains refund semantics';
            end if;
        end if;
    end if;
end $$;

revoke execute on function safebox_billing_release_reservation(text)
    from public, takyon_runtime, takyon_operator_runtime, takyon_app_runtime, takyon_app, safebox;
grant execute on function safebox_billing_release_reservation(text)
    to takyon_safebox_authority;

revoke execute on function safebox_billing_settle(text, bigint)
    from public, takyon_runtime, takyon_operator_runtime, takyon_app_runtime, takyon_app, safebox;
grant execute on function safebox_billing_settle(text, bigint)
    to takyon_safebox_authority;

drop function if exists safebox_billing_refund(text);
