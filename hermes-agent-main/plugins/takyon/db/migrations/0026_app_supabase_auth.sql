-- 0026_app_supabase_auth.sql
-- Sub-user auth pivot (AUTH0.md §7): bind product sub-users to Supabase Auth identities.
--
-- Adds app_users.supabase_user_id — the auth.users(id) uuid minted by Supabase Auth (Google
-- OAuth + email) on the SAME project that hosts this control plane. Identity becomes
-- (business_slug, supabase_user_id) for Supabase-authed sub-users; email stays a mutable
-- attribute and the existing unique (business_slug, email) is unchanged.
--
-- ADDITIVE on purpose: app_magic_links is left in place so the working magic-link path keeps
-- functioning until the Supabase path is verified live; dropping it is a deliberate follow-up,
-- not bundled into the column add (never destroy working auth before its replacement is proven).
--
-- No FK to auth.users here: that table lives in Supabase's `auth` schema, which is absent on the
-- throwaway test Postgres, so a hard FK would make migrations unrunnable off-project. The FK is
-- added separately only against the live Supabase project. The same Google account can be a
-- customer of multiple businesses, so uniqueness is PER business, via a partial unique index that
-- ignores the legacy email-only rows (supabase_user_id is null).

alter table app_users
    add column if not exists supabase_user_id uuid;

create unique index if not exists app_users_business_supabase_uid_uk
    on app_users (business_slug, supabase_user_id)
    where supabase_user_id is not null;
