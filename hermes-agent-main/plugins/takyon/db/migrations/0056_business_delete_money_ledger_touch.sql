-- 0056_business_delete_money_ledger_touch.sql
-- Keep business deletion compatible with the operator/app DB authority split.
--
-- Billing and custody entries are historical money ledgers. The runtime/operator role must not
-- directly UPDATE them, but business deletion still has to detach their nullable business_slug FKs
-- before deleting businesses.slug. This helper is the narrow authority port for exactly that action:
-- count or null business_slug for one slug; no amount, balance, user, or metadata mutation.

create or replace function takyon_business_delete_money_ledger_touch(
    p_business_slug text,
    p_apply boolean default false
)
returns table(ledger_table text, affected integer)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_business text := nullif(btrim(coalesce(p_business_slug, '')), '');
    v_count integer := 0;
begin
    if v_business is null then
        raise exception 'business_slug_required' using errcode = '22023';
    end if;

    if coalesce(p_apply, false) then
        update public.billing_entries
           set business_slug = null
         where business_slug = v_business;
        get diagnostics v_count = row_count;
        ledger_table := 'billing_entries';
        affected := v_count;
        return next;

        update public.custody_entries
           set business_slug = null
         where business_slug = v_business;
        get diagnostics v_count = row_count;
        ledger_table := 'custody_entries';
        affected := v_count;
        return next;
    else
        select count(*)::integer into v_count
          from public.billing_entries
         where business_slug = v_business;
        ledger_table := 'billing_entries';
        affected := v_count;
        return next;

        select count(*)::integer into v_count
          from public.custody_entries
         where business_slug = v_business;
        ledger_table := 'custody_entries';
        affected := v_count;
        return next;
    end if;
end;
$$;

revoke all on function takyon_business_delete_money_ledger_touch(text, boolean) from public;

do $$
declare
    role_name text;
begin
    foreach role_name in array array[
        'takyon_runtime',
        'takyon_operator_runtime',
        'takyon_migration'
    ] loop
        if exists (select 1 from pg_roles where rolname = role_name) then
            execute format(
                'grant execute on function takyon_business_delete_money_ledger_touch(text, boolean) to %I',
                role_name
            );
        end if;
    end loop;
end $$;
