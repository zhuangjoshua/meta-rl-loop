-- 0079_custody_clawback_release.sql
--
-- A won Stripe dispute reverses one earlier custody clawback. The release is append-only:
-- it restores the amount already taken from owed custody (the immediate debit plus later
-- accrual recoveries attributed to that clawback), cancels the target's still-unfunded
-- shortfall, and never credits more than the original clawback request.
--
-- 0076 stored aggregate recovery amounts on accrual metadata. Before releases existed those
-- amounts were necessarily consumed FIFO across clawbacks, so this migration derives that
-- legacy allocation without rewriting history. New accruals persist an explicit FIFO map in
-- `clawback_recovery_allocations`; releases can then cancel one queue item without causing a
-- later recovery to be misattributed to it.

create or replace function takyon_custody_clawback_state(p_user_id uuid)
returns table (
    clawback_entry_id uuid,
    clawback_idempotency_key text,
    business_slug text,
    applied_cents bigint,
    shortfall_cents bigint,
    recovered_cents bigint,
    remaining_cents bigint,
    released boolean,
    created_at timestamptz
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    with clawback_base as (
        select
            e.id as clawback_entry_id,
            e.idempotency_key as clawback_idempotency_key,
            e.business_slug,
            greatest(
                coalesce((e.metadata->>'clawback_applied_cents')::bigint, 0),
                0
            ) as applied_cents,
            greatest(
                coalesce((e.metadata->>'clawback_shortfall_cents')::bigint, 0),
                0
            ) as shortfall_cents,
            e.created_at
        from custody_entries e
        where e.user_id = p_user_id
          and e.kind = 'adjustment'
          and e.metadata->>'custody_clawback' = 'true'
    ),
    ordered_clawbacks as (
        select
            c.*,
            coalesce(
                sum(c.shortfall_cents) over (
                    order by c.created_at, c.clawback_entry_id
                    rows between unbounded preceding and 1 preceding
                ),
                0
            )::bigint as prior_shortfall_cents
        from clawback_base c
    ),
    legacy_recovery as (
        select coalesce(
            sum(greatest(
                coalesce((e.metadata->>'clawback_recovery_cents')::bigint, 0),
                0
            )),
            0
        )::bigint as recovered_cents
        from custody_entries e
        where e.user_id = p_user_id
          and e.kind = 'accrual'
          and e.metadata ? 'clawback_recovery_cents'
          and not (e.metadata ? 'clawback_recovery_allocations')
    ),
    explicit_recovery as (
        select
            c.clawback_entry_id,
            coalesce(sum(
                case
                    when jsonb_typeof(e.metadata->'clawback_recovery_allocations') = 'object'
                    then greatest(coalesce(
                        (e.metadata->'clawback_recovery_allocations'
                            ->>c.clawback_idempotency_key)::bigint,
                        0
                    ), 0)
                    else 0
                end
            ), 0)::bigint as recovered_cents
        from ordered_clawbacks c
        left join custody_entries e
          on e.user_id = p_user_id
         and e.kind = 'accrual'
         and e.metadata ? 'clawback_recovery_allocations'
        group by c.clawback_entry_id
    ),
    attributed as (
        select
            c.*,
            least(
                c.shortfall_cents,
                greatest(l.recovered_cents - c.prior_shortfall_cents, 0)
                + coalesce(x.recovered_cents, 0)
            )::bigint as recovered_cents,
            exists (
                select 1
                from custody_entries r
                where r.user_id = p_user_id
                  and r.kind = 'adjustment'
                  and r.metadata->>'custody_clawback_release' = 'true'
                  and r.metadata->>'clawback_idempotency_key'
                      = c.clawback_idempotency_key
            ) as released
        from ordered_clawbacks c
        cross join legacy_recovery l
        left join explicit_recovery x
          on x.clawback_entry_id = c.clawback_entry_id
    )
    select
        a.clawback_entry_id,
        a.clawback_idempotency_key,
        a.business_slug,
        a.applied_cents,
        a.shortfall_cents,
        a.recovered_cents,
        case
            when a.released then 0
            else greatest(a.shortfall_cents - a.recovered_cents, 0)
        end::bigint as remaining_cents,
        a.released,
        a.created_at
    from attributed a
    order by a.created_at, a.clawback_entry_id;
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
    v_allocations jsonb := '{}'::jsonb;
    v_allocated bigint := 0;
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

    select coalesce(sum(s.remaining_cents), 0)::bigint into v_pending
      from takyon_custody_clawback_state(p_user_id) s
     where not s.released;
    v_recovery := least(greatest(p_net_cents, 0), v_pending);

    with active as (
        select
            s.clawback_idempotency_key,
            s.remaining_cents,
            s.created_at,
            s.clawback_entry_id,
            coalesce(sum(s.remaining_cents) over (
                order by s.created_at, s.clawback_entry_id
                rows between unbounded preceding and 1 preceding
            ), 0)::bigint as prior_remaining_cents
        from takyon_custody_clawback_state(p_user_id) s
        where not s.released and s.remaining_cents > 0
    ), allocations as (
        select
            a.clawback_idempotency_key,
            least(
                a.remaining_cents,
                greatest(v_recovery - a.prior_remaining_cents, 0)
            )::bigint as amount_cents
        from active a
    )
    select
        coalesce(
            jsonb_object_agg(
                q.clawback_idempotency_key,
                q.amount_cents
                order by q.clawback_idempotency_key
            ) filter (where q.amount_cents > 0),
            '{}'::jsonb
        ),
        coalesce(sum(q.amount_cents), 0)::bigint
      into v_allocations, v_allocated
      from allocations q;

    if v_allocated <> v_recovery then
        raise exception 'custody_clawback_recovery_allocation_mismatch'
            using errcode = '23514';
    end if;

    r.new_owed := v_owed + p_net_cents - v_recovery;
    update custody_accounts
       set owed_balance_cents = r.new_owed, updated_at = now()
     where user_id = p_user_id;
    insert into custody_entries (
        user_id, business_slug, kind, gross_cents, fee_cents, net_cents,
        stripe_ref, idempotency_key, metadata
    ) values (
        p_user_id, p_business_slug, 'accrual', p_gross_cents, p_fee_cents,
        p_net_cents - v_recovery, p_stripe_ref, p_idempotency_key,
        (
            coalesce(p_metadata, '{}'::jsonb)
            - 'clawback_recovery_cents'
            - 'clawback_recovery_allocations'
        )
        || case when v_recovery > 0 then jsonb_build_object(
            'clawback_recovery_cents', v_recovery,
            'clawback_recovery_allocations', v_allocations
        ) else '{}'::jsonb end
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

    select coalesce(sum(s.remaining_cents), 0)::bigint into v_pending
      from takyon_custody_clawback_state(p_user_id) s
     where not s.released;
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
        user_id, kind, gross_cents, fee_cents, net_cents,
        stripe_ref, idempotency_key
    ) values (
        p_user_id, 'payout', p_amount_cents, 0, -p_amount_cents,
        p_stripe_ref, p_idempotency_key
    );
    return r;
end;
$$;

do $$
begin
    if not exists (
        select 1
        from pg_type t
        join pg_namespace n on n.oid = t.typnamespace
        where n.nspname = 'public'
          and t.typname = 'safebox_custody_clawback_release_result'
    ) then
        create type safebox_custody_clawback_release_result as (
            refusal text,
            credited_cents bigint,
            new_owed bigint,
            replayed boolean
        );
    end if;
end $$;

create or replace function safebox_custody_release_clawback(
    p_user_id uuid,
    p_business_slug text,
    p_clawback_idempotency_key text,
    p_release_idempotency_key text,
    p_stripe_ref text,
    p_metadata jsonb
)
returns safebox_custody_clawback_release_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r safebox_custody_clawback_release_result;
    v_owed bigint;
    v_existing custody_entries%rowtype;
    v_target custody_entries%rowtype;
    v_prior_release custody_entries%rowtype;
    v_state record;
    v_requested bigint;
    v_credited bigint;
    v_cancelled bigint;
begin
    if session_user not in ('takyon_safebox_authority', 'takyon_migration') then
        raise exception 'safebox_session_required' using errcode = '42501';
    end if;
    if coalesce(trim(p_business_slug), '') = ''
       or coalesce(trim(p_clawback_idempotency_key), '') = ''
       or coalesce(trim(p_release_idempotency_key), '') = '' then
        raise exception 'invalid_custody_clawback_release' using errcode = '22023';
    end if;

    select owed_balance_cents into v_owed
      from custody_accounts where user_id = p_user_id for update;
    if not found then
        r.refusal := 'no_custody_account';
        return r;
    end if;

    select * into v_existing
      from custody_entries
     where idempotency_key = p_release_idempotency_key;
    if found then
        if v_existing.user_id <> p_user_id
           or v_existing.business_slug is distinct from p_business_slug
           or v_existing.kind <> 'adjustment'
           or v_existing.metadata->>'custody_clawback_release' <> 'true'
           or v_existing.metadata->>'clawback_idempotency_key'
              is distinct from p_clawback_idempotency_key then
            raise exception 'custody_clawback_release_idempotency_mismatch'
                using errcode = '22023';
        end if;
        r.credited_cents := greatest(v_existing.net_cents, 0);
        r.new_owed := v_owed;
        r.replayed := true;
        return r;
    end if;

    select * into v_target
      from custody_entries
     where idempotency_key = p_clawback_idempotency_key
       and user_id = p_user_id
       and business_slug = p_business_slug
       and kind = 'adjustment'
       and metadata->>'custody_clawback' = 'true';
    if not found then
        r.refusal := 'custody_clawback_not_found';
        r.new_owed := v_owed;
        return r;
    end if;

    select * into v_prior_release
      from custody_entries e
     where e.user_id = p_user_id
       and e.business_slug = p_business_slug
       and e.kind = 'adjustment'
       and e.metadata->>'custody_clawback_release' = 'true'
       and e.metadata->>'clawback_idempotency_key' = p_clawback_idempotency_key
     order by e.created_at, e.id
     limit 1;
    if found then
        r.credited_cents := greatest(v_prior_release.net_cents, 0);
        r.new_owed := v_owed;
        r.replayed := true;
        return r;
    end if;

    select * into v_state
      from takyon_custody_clawback_state(p_user_id) s
     where s.clawback_idempotency_key = p_clawback_idempotency_key;
    if not found or v_state.released then
        raise exception 'custody_clawback_release_state_invalid' using errcode = '23514';
    end if;

    v_requested := greatest(
        coalesce((v_target.metadata->>'clawback_requested_cents')::bigint, 0),
        0
    );
    if v_state.applied_cents + v_state.shortfall_cents <> v_requested then
        raise exception 'custody_clawback_release_state_invalid' using errcode = '23514';
    end if;
    v_credited := least(
        v_requested,
        greatest(v_state.applied_cents, 0) + greatest(v_state.recovered_cents, 0)
    );
    v_cancelled := greatest(v_state.remaining_cents, 0);
    r.new_owed := v_owed + v_credited;
    r.credited_cents := v_credited;
    r.replayed := false;

    update custody_accounts
       set owed_balance_cents = r.new_owed, updated_at = now()
     where user_id = p_user_id;
    insert into custody_entries (
        user_id, business_slug, kind, gross_cents, fee_cents, net_cents,
        stripe_ref, idempotency_key, metadata
    ) values (
        p_user_id, p_business_slug, 'adjustment', v_credited, 0, v_credited,
        p_stripe_ref, p_release_idempotency_key,
        (
            coalesce(p_metadata, '{}'::jsonb)
            - 'custody_clawback'
            - 'custody_clawback_release'
            - 'clawback_idempotency_key'
            - 'clawback_requested_cents'
            - 'clawback_applied_cents'
            - 'clawback_recovered_cents'
            - 'clawback_cancelled_shortfall_cents'
            - 'clawback_release_credited_cents'
        ) || jsonb_build_object(
            'custody_clawback_release', true,
            'clawback_idempotency_key', p_clawback_idempotency_key,
            'clawback_requested_cents', v_requested,
            'clawback_applied_cents', v_state.applied_cents,
            'clawback_recovered_cents', v_state.recovered_cents,
            'clawback_cancelled_shortfall_cents', v_cancelled,
            'clawback_release_credited_cents', v_credited
        )
    );
    return r;
end;
$$;

alter function takyon_custody_clawback_state(uuid) owner to takyon_migration;
alter type safebox_custody_clawback_release_result owner to takyon_migration;
alter function safebox_custody_accrue(uuid, text, bigint, bigint, bigint, text, text, jsonb)
    owner to takyon_migration;
alter function safebox_custody_payout(uuid, bigint, text, text)
    owner to takyon_migration;
alter function safebox_custody_release_clawback(uuid, text, text, text, text, jsonb)
    owner to takyon_migration;

revoke execute on function takyon_custody_clawback_state(uuid)
    from public, takyon_runtime, takyon_operator_runtime, takyon_app_runtime,
         takyon_app, safebox, takyon_safebox_authority;
revoke execute on function safebox_custody_accrue(
    uuid, text, bigint, bigint, bigint, text, text, jsonb
) from public, takyon_runtime, takyon_operator_runtime, takyon_app_runtime,
       takyon_app, safebox;
revoke execute on function safebox_custody_payout(uuid, bigint, text, text)
    from public, takyon_runtime, takyon_operator_runtime, takyon_app_runtime,
         takyon_app, safebox;
revoke execute on function safebox_custody_release_clawback(
    uuid, text, text, text, text, jsonb
) from public, takyon_runtime, takyon_operator_runtime, takyon_app_runtime,
       takyon_app, safebox;

grant execute on function takyon_custody_clawback_state(uuid) to takyon_migration;
grant execute on function safebox_custody_accrue(
    uuid, text, bigint, bigint, bigint, text, text, jsonb
) to takyon_safebox_authority, takyon_migration;
grant execute on function safebox_custody_payout(uuid, bigint, text, text)
    to takyon_safebox_authority, takyon_migration;
grant execute on function safebox_custody_release_clawback(
    uuid, text, text, text, text, jsonb
) to takyon_safebox_authority, takyon_migration;
