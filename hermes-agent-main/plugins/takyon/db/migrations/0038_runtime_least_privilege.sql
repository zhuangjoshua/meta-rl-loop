-- 0038_runtime_least_privilege.sql
-- Runtime least-privilege boundary (gap G3): demote the runtime DB principal off the money ledgers.
--
-- WHY (the boundary this closes)
-- The runtime connects to Postgres as the database OWNER (a BYPASSRLS role from DATABASE_URL). That
-- principal therefore holds raw INSERT/UPDATE/DELETE on EVERY table — including the operator billing
-- + custody ledgers (0002: billing_accounts/billing_entries/custody_accounts/custody_entries), the
-- business creative-credit ledger (0012: business_creative_credit_accounts/_entries), and
-- businesses.owner_user_id. So anyone holding the runtime DATABASE_URL can fabricate allowance, mint
-- credits, erase custody owed, or re-point business ownership by direct table DML — entirely OUTSIDE
-- the reserve→settle/refund money gates. That is the exact integrity hole the safebox broker exists
-- to remove, and the twin of the app_usage hole 0037 already closed.
--
-- THE FIX (same shape as 0037 + 0030)
-- 1. A new restricted, NON-login, NON-superuser, NON-BYPASSRLS role `takyon_runtime`. NOLOGIN on
--    purpose: this migration must stay INERT for the still-owner-connected runtime. The LOGIN +
--    password and the DATABASE_URL cutover to this role are a SEPARATE out-of-band deploy step; until
--    that step lands, nothing connects as `takyon_runtime`, so applying this migration cannot break
--    the running owner-connected runtime.
-- 2. GRANT-ALL-then-REVOKE-DANGEROUS: grant the role the broad app DML it legitimately needs (so we
--    do NOT have to enumerate every table the runtime writes), then REVOKE the dangerous writes on the
--    six money-ledger tables (retaining SELECT) and column-revoke UPDATE(owner_user_id) on businesses.
-- 3. SECURITY DEFINER money functions (owned by the migration/owner role) that perform the billing +
--    creative-credit writes the demoted role can no longer do directly. Their bodies are a VERBATIM
--    port of the Python row ops in billing.py and business_credits.py — same FOR UPDATE row locks,
--    same balance math, same idempotency short-circuits, same typed refusals. The runtime can only
--    EXECUTE them, so the gate is the ONLY writer; the money math is unchanged.
--
-- RLS INTERACTION — VERIFIED IN CODE (critical, see GAP G3 design step 4)
-- The operator/runtime plane bypasses the 0027 app-plane RLS via the GUC `takyon.rls_bypass`, NOT via
-- the connecting role's BYPASSRLS *attribute*. Proof in the tree:
--   * 0027 policy bodies are all `takyon_rls_bypass() OR (...tenant predicate...)`, and
--     takyon_rls_bypass() (0027) reads `current_setting('takyon.rls_bypass')` — a GUC, not a role attr.
--   * 0030's header states operator connections "keep their privileged login role and the
--     `takyon.rls_bypass='1'` GUC, so they retain full authority through the policies'
--     `takyon_rls_bypass() OR ...` branch."
--   * runtime_app.configure_takyon_pg_session(bypass=True) sets `takyon.rls_bypass='1'` on every store
--     connection open; core._pg_app_scope flips it to '0' (and SET LOCAL ROLE takyon_app) only for the
--     duration of an app-customer scope.
-- Therefore `takyon_runtime` being NOBYPASSRLS is FINE: operator-plane writes pass the RLS policies
-- through the GUC branch regardless of the role's bypass attribute. NOTHING in the bypass path relies
-- on the role's BYPASSRLS attribute (the prior owner role had it, but only the GUC was ever consulted).
-- One consequence handled below: to keep core._pg_app_scope's `SET LOCAL ROLE takyon_app` working when
-- the runtime later connects as `takyon_runtime`, we GRANT takyon_app TO takyon_runtime WITH SET TRUE
-- (mirroring what 0031 does for the current owner login role), and we GRANT takyon_runtime the same
-- app-plane EXECUTEs / SELECTs so the GUC-bypass operator path keeps full authority.
--
-- Idempotent: guarded role create, create-or-replace functions, repeatable grants/revokes. Safe to
-- re-run, and inert for the current owner-connected runtime (nothing logs in as takyon_runtime yet).

-- ── restricted runtime role ──────────────────────────────────────────────────────────────
-- NON-login (cutover is separate), NON-superuser, NON-BYPASSRLS. Mirrors 0030's takyon_app shape but
-- is the FULL runtime principal (the operator/service plane), not the per-customer app-request role.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'takyon_runtime') then
        create role takyon_runtime nologin nosuperuser nobypassrls;
    end if;
end $$;

grant usage on schema public to takyon_runtime;

-- ── grant-all, then revoke-dangerous ─────────────────────────────────────────────────────
-- Broad DML so we never enumerate every table the runtime legitimately writes. ALL TABLES /
-- ALL SEQUENCES covers the current schema; ALTER DEFAULT PRIVILEGES covers tables/sequences a LATER
-- migration creates (applied by the owner, so the default-privilege grant to takyon_runtime fires).
-- (UPDATE on sequences is needed for nextval/setval on serial/bigserial columns.)
grant select, insert, update, delete on all tables in schema public to takyon_runtime;
grant usage, select, update on all sequences in schema public to takyon_runtime;
alter default privileges in schema public
    grant select, insert, update, delete on tables to takyon_runtime;
alter default privileges in schema public
    grant usage, select, update on sequences to takyon_runtime;

-- Close the money-ledger write hole: the runtime principal must NOT directly mutate the six money
-- tables. Writes go through the SECURITY DEFINER functions below; SELECT is retained (reads,
-- reconciliation, and balance derivation still need it).
revoke insert, update, delete on
    billing_accounts,
    billing_entries,
    custody_accounts,
    custody_entries,
    business_creative_credit_accounts,
    business_creative_credit_entries
    from takyon_runtime;

-- Lock business-ownership re-pointing with a COLUMN-level restriction: the runtime may still INSERT a
-- business and UPDATE its other columns (name/mode/goal/status/…), but can NEVER UPDATE owner_user_id
-- by direct DML. (A future re-assignment, if ever needed, would go through a guarded definer func.)
--
-- PG semantics: a table-level `GRANT UPDATE ON businesses` (issued above by GRANT ... ON ALL TABLES)
-- implicitly covers EVERY column, and a bare `REVOKE UPDATE (owner_user_id)` does NOT carve a single
-- column out of that table-wide grant — the table-level UPDATE remains and still permits the column.
-- To make the column restriction bite, the role must NOT hold table-level UPDATE on businesses; it
-- must instead hold UPDATE on ONLY the allowed columns. So: revoke the table-level UPDATE, then grant
-- column UPDATE on every column EXCEPT owner_user_id. Enumerated dynamically from the catalog so a
-- column a LATER migration adds is auto-included (and owner_user_id stays excluded) without editing
-- this file. INSERT/SELECT/DELETE table-level grants are untouched (INSERT still sets owner at create).
revoke update on businesses from takyon_runtime;
do $$
declare
    col_list text;
begin
    select string_agg(format('%I', column_name), ', ')
      into col_list
      from information_schema.columns
     where table_schema = 'public'
       and table_name = 'businesses'
       and column_name <> 'owner_user_id';
    if col_list is not null then
        execute format('grant update (%s) on businesses to takyon_runtime', col_list);
    end if;
end $$;

-- Match 0037's app_usage boundary for this role too: the runtime principal does not directly write
-- the usage ledger; the safebox_*_usage gate functions (0037) are the only sanctioned writer.
revoke insert, update, delete on app_usage_events from takyon_runtime;

-- ── keep the app-customer RLS scope working under the demoted runtime ─────────────────────
-- core._pg_app_scope runs `SET LOCAL ROLE takyon_app` for an app-customer request. When the runtime
-- later connects as takyon_runtime, that SET ROLE needs membership WITH SET (0031 grants the same to
-- the current owner login role). INHERIT FALSE so takyon_runtime does not passively gain takyon_app's
-- (narrower) grants — it must explicitly drop into the scope, exactly as _pg_app_scope does.
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'takyon_app')
       and not exists (
           select 1
           from pg_auth_members m
           join pg_roles grp on grp.oid = m.roleid
           join pg_roles mem on mem.oid = m.member
           where grp.rolname = 'takyon_app'
             and mem.rolname = 'takyon_runtime'
             and m.set_option
       )
    then
        grant takyon_app to takyon_runtime with inherit false, set true;
    end if;
end $$;

-- The operator/runtime plane reaches the app-plane tables through the GUC-bypass branch (NOT via
-- SET ROLE takyon_app). Grant the same EXECUTEs/SELECTs 0030 gave takyon_app so the demoted runtime
-- retains full operator authority on the app plane (RLS still passes via the takyon.rls_bypass GUC).
grant execute on function takyon_rls_bypass() to takyon_runtime;
grant execute on function takyon_rls_business_slug() to takyon_runtime;
grant execute on function takyon_rls_bound_app_user_id() to takyon_runtime;
grant execute on function takyon_rls_session_hash() to takyon_runtime;
grant execute on function takyon_rls_effective_app_user_id() to takyon_runtime;
grant execute on function takyon_rls_effective_email() to takyon_runtime;

-- The 0037 usage-gate functions: grant EXECUTE to takyon_runtime too (it can no longer write
-- app_usage_events directly, exactly like takyon_app).
grant execute on function safebox_reserve_usage(
    text, bigint, text, uuid, bigint, text, text, text, text, text, jsonb) to takyon_runtime;
grant execute on function safebox_settle_usage(
    text, text, bigint, integer, integer, text, text, text, jsonb) to takyon_runtime;
grant execute on function safebox_release_usage(text, text, text, jsonb) to takyon_runtime;
grant execute on function safebox_reconcile_held_usage(bigint) to takyon_runtime;

-- ══════════════════════════════════════════════════════════════════════════════════════════
-- SECURITY DEFINER money functions — the demoted role's only path to the ledgers.
-- Each is a VERBATIM port of the Python row ops; refusals come back as a `refusal` discriminator
-- carrying the exact figures the typed Python exceptions need, so the Python wrappers re-raise the
-- identical exceptions (InsufficientBalance / InsufficientCreativeCredits / NoBillingAccount /
-- UnknownReservation / UnknownCreativeCreditReservation) without parsing strings. SECURITY DEFINER
-- with a pinned search_path so the function runs with the owner's table privileges.
-- ══════════════════════════════════════════════════════════════════════════════════════════

-- ── billing (0002 flow A: user → platform) ───────────────────────────────────────────────
-- Result composite for the billing ops that can refuse. `refusal` is NULL on success.
--   refusal codes: 'no_billing_account' (NoBillingAccount), 'unknown_reservation' (UnknownReservation),
--                  'insufficient_balance' (InsufficientBalance + figures).
-- fig_* carry InsufficientBalance's exact fields. account_* carry the post-op cached figures grant
-- needs to return the in-effect included amount.
drop type if exists safebox_billing_result cascade;
create type safebox_billing_result as (
    refusal                       text,
    fig_estimate_cents            bigint,   -- InsufficientBalance.estimate_cents
    fig_allowance_available_cents bigint,   -- InsufficientBalance.allowance_available_cents
    allowance_cents               bigint,   -- reserve: the held allowance (Reservation.allowance_cents)
    included_cents                bigint     -- grant: the included amount in effect
);

-- safebox_billing_open_account — verbatim port of billing.open_billing_account (idempotent insert).
create or replace function safebox_billing_open_account(
    p_user_id uuid,
    p_allowance_included_cents bigint
)
returns void
language sql
security definer
set search_path = public, pg_temp
as $$
    insert into billing_accounts (user_id, allowance_included_cents)
    values (p_user_id, p_allowance_included_cents)
    on conflict (user_id) do nothing;
$$;

-- safebox_billing_grant_allowance — verbatim port of billing.grant_allowance. Locks the account row,
-- idempotent on idempotency_key (the entry-exists check), sets the period included + resets used to 0,
-- writes the 'grant' entry. NoBillingAccount when the row is absent.
create or replace function safebox_billing_grant_allowance(
    p_user_id         uuid,
    p_included_cents   bigint,
    p_idempotency_key  text,
    p_period_start     timestamptz,
    p_resets_at        timestamptz
)
returns safebox_billing_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r          safebox_billing_result;
    v_included bigint;
    v_exists   boolean;
begin
    select allowance_included_cents into v_included
        from billing_accounts where user_id = p_user_id for update;
    if not found then
        r.refusal := 'no_billing_account';
        return r;
    end if;
    -- _entry_exists(idempotency_key): replay returns the current included amount, writes nothing.
    select exists (
        select 1 from billing_entries where idempotency_key = p_idempotency_key
    ) into v_exists;
    if v_exists then
        r.included_cents := v_included;
        return r;
    end if;
    update billing_accounts set
        allowance_included_cents = p_included_cents,
        allowance_used_cents = 0,
        allowance_period_start = coalesce(p_period_start, now()),
        allowance_resets_at = p_resets_at,
        updated_at = now()
        where user_id = p_user_id;
    insert into billing_entries
        (user_id, bucket, kind, amount_cents, balance_after_cents, idempotency_key)
        values (p_user_id, 'allowance', 'grant', p_included_cents, 0, p_idempotency_key);
    r.included_cents := p_included_cents;
    return r;
end;
$$;

-- safebox_billing_reserve — verbatim port of billing.reserve. Locks the account row, idempotent on
-- the reservation_key (the prior 'reserve' entries), refuses InsufficientBalance (nothing written),
-- else holds the estimate against the allowance and writes the 'reserve' entry.
create or replace function safebox_billing_reserve(
    p_user_id        uuid,
    p_estimate_cents  bigint,
    p_reservation_key text,
    p_business_slug   text,
    p_job_id          text
)
returns safebox_billing_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r              safebox_billing_result;
    v_included     bigint;
    v_used         bigint;
    v_existing_allow bigint;
    v_avail_allow  bigint;
    v_new_used     bigint;
begin
    select allowance_included_cents, allowance_used_cents into v_included, v_used
        from billing_accounts where user_id = p_user_id for update;
    if not found then
        r.refusal := 'no_billing_account';
        return r;
    end if;
    -- Idempotent on reservation_key: a replay returns the SAME held allowance, writes nothing.
    select coalesce(sum(amount_cents) filter (where bucket = 'allowance'), 0)
        into v_existing_allow
        from billing_entries
        where reservation_key = p_reservation_key and kind = 'reserve';
    if exists (
        select 1 from billing_entries
        where reservation_key = p_reservation_key and kind = 'reserve'
    ) then
        r.allowance_cents := v_existing_allow;
        return r;
    end if;
    v_avail_allow := greatest(0, v_included - v_used);
    if p_estimate_cents > v_avail_allow then
        r.refusal := 'insufficient_balance';
        r.fig_estimate_cents := p_estimate_cents;
        r.fig_allowance_available_cents := v_avail_allow;
        return r;
    end if;
    v_new_used := v_used + p_estimate_cents;
    update billing_accounts set allowance_used_cents = v_new_used, updated_at = now()
        where user_id = p_user_id;
    -- Allowance entry: always written (even a zero anchor) so the reservation_key replays idempotently.
    insert into billing_entries
        (user_id, business_slug, bucket, kind, amount_cents, balance_after_cents,
         reservation_key, job_id, idempotency_key)
        values (p_user_id, p_business_slug, 'allowance', 'reserve', p_estimate_cents, v_new_used,
                p_reservation_key, p_job_id, p_reservation_key || ':reserve:allowance');
    r.allowance_cents := p_estimate_cents;
    return r;
end;
$$;

-- safebox_billing_settle — verbatim port of billing.settle. Looks up the reserve entries, locks the
-- account row, idempotent (already-finalized → no-op), records the actual spend and releases the
-- unused remainder. Raises (via SQL exception, not a refusal) on the ValueError preconditions that
-- billing.py itself validates in Python BEFORE the row ops; here we only port the row ops. The Python
-- wrapper keeps its own pre-checks, so the actual<=reserved invariant is enforced before this call.
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
    r          safebox_billing_result;
    v_user_id  uuid;
    v_a_resv   bigint;
    v_used     bigint;
    v_finalized boolean;
    v_s_alloc  bigint;
    v_r_alloc  bigint;
    v_new_used bigint;
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
    select allowance_used_cents into v_used
        from billing_accounts where user_id = v_user_id for update;
    -- _finalized: a prior settle/refund means this is a no-op (first finalizer wins).
    select exists (
        select 1 from billing_entries
        where reservation_key = p_reservation_key and kind in ('settle', 'refund')
    ) into v_finalized;
    if v_finalized then
        return r;  -- refusal NULL, no-op
    end if;
    v_s_alloc := p_actual_cents;
    v_r_alloc := v_a_resv - v_s_alloc;
    v_new_used := v_used - v_r_alloc;
    update billing_accounts set allowance_used_cents = v_new_used, updated_at = now()
        where user_id = v_user_id;
    -- settle reclassifies held → spent (balance_after = pre-release used); refund carries released.
    if v_s_alloc > 0 then
        insert into billing_entries
            (user_id, bucket, kind, amount_cents, balance_after_cents, reservation_key, idempotency_key)
            values (v_user_id, 'allowance', 'settle', v_s_alloc, v_used, p_reservation_key,
                    p_reservation_key || ':settle:allowance');
    end if;
    if v_r_alloc > 0 then
        insert into billing_entries
            (user_id, bucket, kind, amount_cents, balance_after_cents, reservation_key, idempotency_key)
            values (v_user_id, 'allowance', 'refund', v_r_alloc, v_new_used, p_reservation_key,
                    p_reservation_key || ':refund:allowance');
    end if;
    return r;
end;
$$;

-- safebox_billing_refund — verbatim port of billing.refund. Releases the whole reservation (failure
-- path). Idempotent (already-finalized → no-op). UnknownReservation when no reserve entry exists.
create or replace function safebox_billing_refund(
    p_reservation_key text
)
returns safebox_billing_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r          safebox_billing_result;
    v_user_id  uuid;
    v_a_resv   bigint;
    v_used     bigint;
    v_finalized boolean;
    v_new_used bigint;
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
    select allowance_used_cents into v_used
        from billing_accounts where user_id = v_user_id for update;
    select exists (
        select 1 from billing_entries
        where reservation_key = p_reservation_key and kind in ('settle', 'refund')
    ) into v_finalized;
    if v_finalized then
        return r;  -- no-op
    end if;
    v_new_used := v_used - v_a_resv;
    update billing_accounts set allowance_used_cents = v_new_used, updated_at = now()
        where user_id = v_user_id;
    if v_a_resv > 0 then
        insert into billing_entries
            (user_id, bucket, kind, amount_cents, balance_after_cents, reservation_key, idempotency_key)
            values (v_user_id, 'allowance', 'refund', v_a_resv, v_new_used, p_reservation_key,
                    p_reservation_key || ':refund:allowance');
    end if;
    return r;
end;
$$;

grant execute on function safebox_billing_open_account(uuid, bigint) to takyon_runtime;
grant execute on function safebox_billing_grant_allowance(uuid, bigint, text, timestamptz, timestamptz) to takyon_runtime;
grant execute on function safebox_billing_reserve(uuid, bigint, text, text, text) to takyon_runtime;
grant execute on function safebox_billing_settle(text, bigint) to takyon_runtime;
grant execute on function safebox_billing_refund(text) to takyon_runtime;

-- ── creative credits (0012: business-scoped) ─────────────────────────────────────────────
-- Result composite for the credit ops that can refuse. `refusal` is NULL on success.
--   refusal codes: 'insufficient_credits' (InsufficientCreativeCredits + figures),
--                  'unknown_reservation' (UnknownCreativeCreditReservation).
-- balance_credits + reserved_credits carry the post-op CreativeCreditBalances; reserved_credits_out
-- carries the held amount for reserve (CreativeCreditReservation.reserved_credits).
drop type if exists safebox_credits_result cascade;
create type safebox_credits_result as (
    refusal                  text,
    fig_requested_credits    bigint,   -- InsufficientCreativeCredits.requested_credits
    fig_available_credits    bigint,   -- InsufficientCreativeCredits.available_credits
    business_slug            text,
    balance_credits          bigint,
    reserved_credits         bigint,   -- derived reserved (the _reserved_credits() aggregate)
    reserved_credits_out     bigint     -- reserve: the held amount
);

-- Helper: the _reserved_credits() aggregate — Σ reserve amounts with no matching commit/release.
create or replace function safebox_credits_reserved(p_business_slug text)
returns bigint
language sql
security definer
set search_path = public, pg_temp
as $$
    select coalesce(sum(r.amount_credits), 0)::bigint
    from business_creative_credit_entries r
    left join business_creative_credit_entries f
      on f.reservation_key = r.reservation_key
     and f.kind in ('commit', 'release')
    where r.business_slug = p_business_slug
      and r.kind = 'reserve'
      and f.id is null;
$$;

-- safebox_credits_open_account — verbatim port of open_business_credit_account.
create or replace function safebox_credits_open_account(p_business_slug text)
returns void
language sql
security definer
set search_path = public, pg_temp
as $$
    insert into business_creative_credit_accounts (business_slug)
    values (p_business_slug)
    on conflict (business_slug) do nothing;
$$;

-- safebox_credits_grant — verbatim port of grant_credits. Locks the account row (_ensure_account_
-- locked), idempotent on stripe_ref (per-business 'grant') AND on idempotency_key, credits the pack.
create or replace function safebox_credits_grant(
    p_business_slug  text,
    p_credits         bigint,
    p_idempotency_key text,
    p_metadata        jsonb,
    p_stripe_ref      text
)
returns safebox_credits_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r           safebox_credits_result;
    v_balance   bigint;
    v_new_balance bigint;
begin
    -- _ensure_account_locked: open if absent, then lock the row.
    insert into business_creative_credit_accounts (business_slug)
        values (p_business_slug) on conflict (business_slug) do nothing;
    select balance_credits into v_balance
        from business_creative_credit_accounts where business_slug = p_business_slug for update;
    -- stripe_ref idempotency (per business, kind='grant'): replay returns current balances.
    if p_stripe_ref is not null and exists (
        select 1 from business_creative_credit_entries
        where business_slug = p_business_slug and kind = 'grant' and stripe_ref = p_stripe_ref
    ) then
        r.business_slug := p_business_slug;
        r.balance_credits := v_balance;
        r.reserved_credits := safebox_credits_reserved(p_business_slug);
        return r;
    end if;
    -- idempotency_key idempotency (global unique): replay returns current balances.
    if exists (
        select 1 from business_creative_credit_entries where idempotency_key = p_idempotency_key
    ) then
        r.business_slug := p_business_slug;
        r.balance_credits := v_balance;
        r.reserved_credits := safebox_credits_reserved(p_business_slug);
        return r;
    end if;
    v_new_balance := v_balance + p_credits;
    update business_creative_credit_accounts
        set balance_credits = v_new_balance, updated_at = now()
        where business_slug = p_business_slug;
    insert into business_creative_credit_entries
        (business_slug, kind, amount_credits, balance_after_credits, idempotency_key, metadata, stripe_ref)
        values (p_business_slug, 'grant', p_credits, v_new_balance, p_idempotency_key,
                coalesce(p_metadata, '{}'::jsonb), p_stripe_ref);
    r.business_slug := p_business_slug;
    r.balance_credits := v_new_balance;
    r.reserved_credits := safebox_credits_reserved(p_business_slug);
    return r;
end;
$$;

-- safebox_credits_reserve — verbatim port of reserve_credits. Locks the account row, idempotent on
-- the reservation_key, refuses InsufficientCreativeCredits (nothing written), else holds the credits.
create or replace function safebox_credits_reserve(
    p_business_slug  text,
    p_credits         bigint,
    p_reservation_key text,
    p_metadata        jsonb
)
returns safebox_credits_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r              safebox_credits_result;
    v_balance      bigint;
    v_existing     bigint;
    v_new_balance  bigint;
begin
    insert into business_creative_credit_accounts (business_slug)
        values (p_business_slug) on conflict (business_slug) do nothing;
    select balance_credits into v_balance
        from business_creative_credit_accounts where business_slug = p_business_slug for update;
    -- Idempotent on reservation_key: replay returns the SAME held amount, writes nothing.
    select amount_credits into v_existing
        from business_creative_credit_entries
        where reservation_key = p_reservation_key and kind = 'reserve';
    if found then
        r.reserved_credits_out := v_existing;
        return r;
    end if;
    if p_credits > v_balance then
        r.refusal := 'insufficient_credits';
        r.fig_requested_credits := p_credits;
        r.fig_available_credits := v_balance;
        return r;
    end if;
    v_new_balance := v_balance - p_credits;
    update business_creative_credit_accounts
        set balance_credits = v_new_balance, updated_at = now()
        where business_slug = p_business_slug;
    insert into business_creative_credit_entries
        (business_slug, kind, amount_credits, balance_after_credits, reservation_key, idempotency_key, metadata)
        values (p_business_slug, 'reserve', p_credits, v_new_balance, p_reservation_key,
                p_reservation_key, coalesce(p_metadata, '{}'::jsonb));
    r.reserved_credits_out := p_credits;
    return r;
end;
$$;

-- safebox_credits_commit — verbatim port of commit_credits. Locks the reserve row + account row,
-- idempotent (prior commit/release → no-op returning current balances), refunds reserved−actual.
-- The actual<=reserved + actual>=0 preconditions stay in Python (ValueError before this call).
create or replace function safebox_credits_commit(
    p_reservation_key text,
    p_actual_credits   bigint,   -- NULL → spend the whole reservation
    p_metadata         jsonb
)
returns safebox_credits_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r               safebox_credits_result;
    v_business_slug text;
    v_reserved      bigint;
    v_balance       bigint;
    v_spent         bigint;
    v_refund        bigint;
    v_new_balance   bigint;
begin
    select business_slug, amount_credits into v_business_slug, v_reserved
        from business_creative_credit_entries
        where reservation_key = p_reservation_key and kind = 'reserve'
        for update;
    if not found then
        r.refusal := 'unknown_reservation';
        return r;
    end if;
    -- _ensure_account_locked on the reservation's business.
    insert into business_creative_credit_accounts (business_slug)
        values (v_business_slug) on conflict (business_slug) do nothing;
    select balance_credits into v_balance
        from business_creative_credit_accounts where business_slug = v_business_slug for update;
    -- Prior commit/release → no-op returning current balances.
    if exists (
        select 1 from business_creative_credit_entries
        where reservation_key = p_reservation_key and kind in ('commit', 'release')
    ) then
        r.business_slug := v_business_slug;
        r.balance_credits := v_balance;
        r.reserved_credits := safebox_credits_reserved(v_business_slug);
        return r;
    end if;
    v_spent := coalesce(p_actual_credits, v_reserved);
    v_refund := v_reserved - v_spent;
    v_new_balance := v_balance + v_refund;
    update business_creative_credit_accounts
        set balance_credits = v_new_balance, updated_at = now()
        where business_slug = v_business_slug;
    insert into business_creative_credit_entries
        (business_slug, kind, amount_credits, balance_after_credits, reservation_key, idempotency_key, metadata)
        values (v_business_slug, 'commit', v_spent, v_new_balance, p_reservation_key,
                p_reservation_key || ':commit', coalesce(p_metadata, '{}'::jsonb));
    r.business_slug := v_business_slug;
    r.balance_credits := v_new_balance;
    r.reserved_credits := safebox_credits_reserved(v_business_slug);
    return r;
end;
$$;

-- safebox_credits_release — verbatim port of release_credits. Locks the reserve row + account row,
-- idempotent (prior commit/release → no-op), returns the full reservation to balance.
create or replace function safebox_credits_release(
    p_reservation_key text,
    p_metadata         jsonb
)
returns safebox_credits_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r               safebox_credits_result;
    v_business_slug text;
    v_reserved      bigint;
    v_balance       bigint;
    v_new_balance   bigint;
begin
    select business_slug, amount_credits into v_business_slug, v_reserved
        from business_creative_credit_entries
        where reservation_key = p_reservation_key and kind = 'reserve'
        for update;
    if not found then
        r.refusal := 'unknown_reservation';
        return r;
    end if;
    insert into business_creative_credit_accounts (business_slug)
        values (v_business_slug) on conflict (business_slug) do nothing;
    select balance_credits into v_balance
        from business_creative_credit_accounts where business_slug = v_business_slug for update;
    if exists (
        select 1 from business_creative_credit_entries
        where reservation_key = p_reservation_key and kind in ('commit', 'release')
    ) then
        r.business_slug := v_business_slug;
        r.balance_credits := v_balance;
        r.reserved_credits := safebox_credits_reserved(v_business_slug);
        return r;
    end if;
    v_new_balance := v_balance + v_reserved;
    update business_creative_credit_accounts
        set balance_credits = v_new_balance, updated_at = now()
        where business_slug = v_business_slug;
    insert into business_creative_credit_entries
        (business_slug, kind, amount_credits, balance_after_credits, reservation_key, idempotency_key, metadata)
        values (v_business_slug, 'release', v_reserved, v_new_balance, p_reservation_key,
                p_reservation_key || ':release', coalesce(p_metadata, '{}'::jsonb));
    r.business_slug := v_business_slug;
    r.balance_credits := v_new_balance;
    r.reserved_credits := safebox_credits_reserved(v_business_slug);
    return r;
end;
$$;

grant execute on function safebox_credits_reserved(text) to takyon_runtime;
grant execute on function safebox_credits_open_account(text) to takyon_runtime;
grant execute on function safebox_credits_grant(text, bigint, text, jsonb, text) to takyon_runtime;
grant execute on function safebox_credits_reserve(text, bigint, text, jsonb) to takyon_runtime;
grant execute on function safebox_credits_commit(text, bigint, jsonb) to takyon_runtime;
grant execute on function safebox_credits_release(text, jsonb) to takyon_runtime;

-- ── custody (0002 flow B: sub-users → user, held by the platform) ─────────────────────────
-- The custody tables are in the REVOKE list above, so the demoted runtime cannot write them
-- directly. accrue/payout therefore route through these SECURITY DEFINER ports so flow-B accrual +
-- payout keep working after the DSN cutover (the capability must not silently break — fail-closed
-- with NO path would block real Stripe-webhook accrual). Fee math + the owed≥amount payout gate stay
-- in Python (custody.py); these funcs are the verbatim row ops under the account row lock.
--   refusal codes: 'no_custody_account' (NoCustodyAccount), 'insufficient_custody' (InsufficientCustody
--                  + figures). new_owed carries the post-op owed balance the Python op returns.
drop type if exists safebox_custody_result cascade;
create type safebox_custody_result as (
    refusal              text,
    fig_requested_cents  bigint,   -- InsufficientCustody.requested_cents
    fig_owed_cents       bigint,   -- InsufficientCustody.owed_cents
    new_owed             bigint
);

-- safebox_custody_open_account — verbatim port of custody.open_custody_account.
create or replace function safebox_custody_open_account(
    p_user_id  uuid,
    p_currency  text
)
returns void
language sql
security definer
set search_path = public, pg_temp
as $$
    insert into custody_accounts (user_id, currency)
    values (p_user_id, p_currency)
    on conflict (user_id) do nothing;
$$;

-- safebox_custody_accrue — verbatim port of custody.accrue's row ops. fee/net/withheld are computed
-- in Python and passed in (the policy math stays in custody.py); this is the atomic row-op body:
-- lock the account, NoCustodyAccount when absent, idempotent on idempotency_key (replay returns owed),
-- else accrue net to owed + write the 'accrual' entry.
create or replace function safebox_custody_accrue(
    p_user_id        uuid,
    p_business_slug  text,
    p_gross_cents    bigint,
    p_fee_cents      bigint,
    p_net_cents      bigint,
    p_idempotency_key text,
    p_stripe_ref     text,
    p_metadata       jsonb
)
returns safebox_custody_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r         safebox_custody_result;
    v_owed    bigint;
    v_exists  boolean;
    v_new_owed bigint;
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
        r.new_owed := v_owed;  -- replay: return current owed, write nothing
        return r;
    end if;
    v_new_owed := v_owed + p_net_cents;
    update custody_accounts set owed_balance_cents = v_new_owed, updated_at = now()
        where user_id = p_user_id;
    insert into custody_entries
        (user_id, business_slug, kind, gross_cents, fee_cents, net_cents, stripe_ref, idempotency_key, metadata)
        values (p_user_id, p_business_slug, 'accrual', p_gross_cents, p_fee_cents, p_net_cents,
                p_stripe_ref, p_idempotency_key, coalesce(p_metadata, '{}'::jsonb));
    r.new_owed := v_new_owed;
    return r;
end;
$$;

-- safebox_custody_payout — verbatim port of custody.payout's row ops. Lock the account, NoCustody
-- Account when absent, idempotent on idempotency_key (replay returns owed), InsufficientCustody when
-- amount>owed (nothing written), else drain owed + bump paid_out + write the 'payout' entry.
create or replace function safebox_custody_payout(
    p_user_id        uuid,
    p_amount_cents   bigint,
    p_idempotency_key text,
    p_stripe_ref     text
)
returns safebox_custody_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r          safebox_custody_result;
    v_owed     bigint;
    v_paid_out bigint;
    v_exists   boolean;
    v_new_owed bigint;
    v_new_paid bigint;
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
        r.new_owed := v_owed;  -- replay
        return r;
    end if;
    if p_amount_cents > v_owed then
        r.refusal := 'insufficient_custody';
        r.fig_requested_cents := p_amount_cents;
        r.fig_owed_cents := v_owed;
        return r;
    end if;
    v_new_owed := v_owed - p_amount_cents;
    v_new_paid := v_paid_out + p_amount_cents;
    update custody_accounts set owed_balance_cents = v_new_owed, paid_out_cents = v_new_paid,
        updated_at = now() where user_id = p_user_id;
    insert into custody_entries
        (user_id, kind, gross_cents, fee_cents, net_cents, stripe_ref, idempotency_key)
        values (p_user_id, 'payout', p_amount_cents, 0, -p_amount_cents, p_stripe_ref, p_idempotency_key);
    r.new_owed := v_new_owed;
    return r;
end;
$$;

grant execute on function safebox_custody_open_account(uuid, text) to takyon_runtime;
grant execute on function safebox_custody_accrue(uuid, text, bigint, bigint, bigint, text, text, jsonb) to takyon_runtime;
grant execute on function safebox_custody_payout(uuid, bigint, text, text) to takyon_runtime;
