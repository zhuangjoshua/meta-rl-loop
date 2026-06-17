-- 0027_app_plane_rls.sql
-- App-plane Postgres RLS: DB-enforced customer scope for the shared product substrate.
--
-- The runtime already scopes app reads/writes by business + session/app_user in Python. This adds
-- the database backstop for the shared app tables so a stray query cannot silently jump customers.
-- Internal operator/service connections default to bypass mode via runtime-app connection options;
-- app-facing routes deliberately flip that bypass off for one request-local scope.

create or replace function takyon_rls_bypass()
returns boolean
language sql
stable
as $$
    select coalesce(nullif(current_setting('takyon.rls_bypass', true), ''), '0') in ('1', 'true', 'on');
$$;

create or replace function takyon_rls_business_slug()
returns text
language sql
stable
as $$
    select nullif(current_setting('takyon.rls_business_slug', true), '');
$$;

create or replace function takyon_rls_bound_app_user_id()
returns uuid
language sql
stable
as $$
    select nullif(current_setting('takyon.rls_app_user_id', true), '')::uuid;
$$;

create or replace function takyon_rls_session_hash()
returns text
language sql
stable
as $$
    select nullif(current_setting('takyon.rls_session_hash', true), '');
$$;

create or replace function takyon_rls_effective_app_user_id()
returns uuid
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select coalesce(
        takyon_rls_bound_app_user_id(),
        (
            select s.app_user_id
            from app_sessions s
            where s.business_slug = takyon_rls_business_slug()
              and s.token_hash = takyon_rls_session_hash()
              and s.revoked_at is null
              and s.expires_at > now()
            limit 1
        )
    );
$$;

create or replace function takyon_rls_effective_email()
returns text
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select coalesce(
        (
            select lower(u.email::text)
            from app_users u
            where u.business_slug = takyon_rls_business_slug()
              and u.id = takyon_rls_effective_app_user_id()
            limit 1
        ),
        ''
    );
$$;

alter table if exists app_user_profiles enable row level security;
alter table if exists app_user_profiles force row level security;
drop policy if exists takyon_app_user_profiles_select on app_user_profiles;
create policy takyon_app_user_profiles_select
    on app_user_profiles
    for select
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and takyon_rls_effective_app_user_id() is not null
            and (id = takyon_rls_effective_app_user_id() or directory_enabled)
        )
    );
drop policy if exists takyon_app_user_profiles_insert on app_user_profiles;
create policy takyon_app_user_profiles_insert
    on app_user_profiles
    for insert
    with check (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and id = takyon_rls_effective_app_user_id()
        )
    );
drop policy if exists takyon_app_user_profiles_update on app_user_profiles;
create policy takyon_app_user_profiles_update
    on app_user_profiles
    for update
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and id = takyon_rls_effective_app_user_id()
        )
    )
    with check (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and id = takyon_rls_effective_app_user_id()
        )
    );
drop policy if exists takyon_app_user_profiles_delete on app_user_profiles;
create policy takyon_app_user_profiles_delete
    on app_user_profiles
    for delete
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and id = takyon_rls_effective_app_user_id()
        )
    );

alter table if exists app_records enable row level security;
alter table if exists app_records force row level security;
drop policy if exists takyon_app_records_select on app_records;
create policy takyon_app_records_select
    on app_records
    for select
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and app_user_id = takyon_rls_effective_app_user_id()
        )
    );
drop policy if exists takyon_app_records_insert on app_records;
create policy takyon_app_records_insert
    on app_records
    for insert
    with check (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and app_user_id = takyon_rls_effective_app_user_id()
        )
    );
drop policy if exists takyon_app_records_update on app_records;
create policy takyon_app_records_update
    on app_records
    for update
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and app_user_id = takyon_rls_effective_app_user_id()
        )
    )
    with check (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and app_user_id = takyon_rls_effective_app_user_id()
        )
    );
drop policy if exists takyon_app_records_delete on app_records;
create policy takyon_app_records_delete
    on app_records
    for delete
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and app_user_id = takyon_rls_effective_app_user_id()
        )
    );

alter table if exists app_connections enable row level security;
alter table if exists app_connections force row level security;
drop policy if exists takyon_app_connections_select on app_connections;
create policy takyon_app_connections_select
    on app_connections
    for select
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and takyon_rls_effective_app_user_id() is not null
            and (
                source_app_user_id = takyon_rls_effective_app_user_id()
                or target_app_user_id = takyon_rls_effective_app_user_id()
            )
        )
    );
drop policy if exists takyon_app_connections_insert on app_connections;
create policy takyon_app_connections_insert
    on app_connections
    for insert
    with check (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and source_app_user_id = takyon_rls_effective_app_user_id()
        )
    );
drop policy if exists takyon_app_connections_update on app_connections;
create policy takyon_app_connections_update
    on app_connections
    for update
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and source_app_user_id = takyon_rls_effective_app_user_id()
        )
    )
    with check (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and source_app_user_id = takyon_rls_effective_app_user_id()
        )
    );
drop policy if exists takyon_app_connections_delete on app_connections;
create policy takyon_app_connections_delete
    on app_connections
    for delete
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and source_app_user_id = takyon_rls_effective_app_user_id()
        )
    );

alter table if exists app_entitlements enable row level security;
alter table if exists app_entitlements force row level security;
drop policy if exists takyon_app_entitlements_select on app_entitlements;
create policy takyon_app_entitlements_select
    on app_entitlements
    for select
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and app_user_id = takyon_rls_effective_app_user_id()
        )
    );
drop policy if exists takyon_app_entitlements_write on app_entitlements;
create policy takyon_app_entitlements_write
    on app_entitlements
    for all
    using (takyon_rls_bypass())
    with check (takyon_rls_bypass());

alter table if exists app_usage_events enable row level security;
alter table if exists app_usage_events force row level security;
drop policy if exists takyon_app_usage_events_select on app_usage_events;
create policy takyon_app_usage_events_select
    on app_usage_events
    for select
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and app_user_id = takyon_rls_effective_app_user_id()
        )
    );
drop policy if exists takyon_app_usage_events_write on app_usage_events;
create policy takyon_app_usage_events_write
    on app_usage_events
    for all
    using (takyon_rls_bypass())
    with check (takyon_rls_bypass());

alter table if exists app_revenue_events enable row level security;
alter table if exists app_revenue_events force row level security;
drop policy if exists takyon_app_revenue_events_select on app_revenue_events;
create policy takyon_app_revenue_events_select
    on app_revenue_events
    for select
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and takyon_rls_effective_email() <> ''
            and lower(coalesce(customer_email, '')) = takyon_rls_effective_email()
        )
    );
drop policy if exists takyon_app_revenue_events_write on app_revenue_events;
create policy takyon_app_revenue_events_write
    on app_revenue_events
    for all
    using (takyon_rls_bypass())
    with check (takyon_rls_bypass());

alter table if exists app_checkout_intents enable row level security;
alter table if exists app_checkout_intents force row level security;
drop policy if exists takyon_app_checkout_intents_select on app_checkout_intents;
create policy takyon_app_checkout_intents_select
    on app_checkout_intents
    for select
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and takyon_rls_effective_app_user_id() is not null
            and (
                app_user_id = takyon_rls_effective_app_user_id()
                or (
                    takyon_rls_effective_email() <> ''
                    and lower(coalesce(customer_email, '')) = takyon_rls_effective_email()
                )
            )
        )
    );
drop policy if exists takyon_app_checkout_intents_write on app_checkout_intents;
create policy takyon_app_checkout_intents_write
    on app_checkout_intents
    for all
    using (takyon_rls_bypass())
    with check (takyon_rls_bypass());

alter table if exists app_checkout_sessions enable row level security;
alter table if exists app_checkout_sessions force row level security;
drop policy if exists takyon_app_checkout_sessions_select on app_checkout_sessions;
create policy takyon_app_checkout_sessions_select
    on app_checkout_sessions
    for select
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and takyon_rls_effective_email() <> ''
            and lower(coalesce(customer_email, '')) = takyon_rls_effective_email()
        )
    );
drop policy if exists takyon_app_checkout_sessions_write on app_checkout_sessions;
create policy takyon_app_checkout_sessions_write
    on app_checkout_sessions
    for all
    using (takyon_rls_bypass())
    with check (takyon_rls_bypass());

alter table if exists app_media enable row level security;
alter table if exists app_media force row level security;
drop policy if exists takyon_app_media_select on app_media;
create policy takyon_app_media_select
    on app_media
    for select
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and app_user_id = coalesce(takyon_rls_effective_app_user_id()::text, '')
        )
    );
drop policy if exists takyon_app_media_write on app_media;
create policy takyon_app_media_write
    on app_media
    for all
    using (takyon_rls_bypass())
    with check (takyon_rls_bypass());
