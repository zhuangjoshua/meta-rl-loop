-- 0001_identity_spine.sql
-- Takyon control plane (Postgres / Supabase target) — P1 identity spine.
--
-- Creates the top-level Takyon operator accounts, the single opaque per-user API
-- key, and first-class business ownership. This is the canonical source of truth
-- for the eventual Postgres control plane; the current SQLite control plane in
-- plugins/takyon/core.py (TakyonStore) is being superseded per mediationplan.md.
--
-- Idempotent: safe to run repeatedly. On a clean `public` (local test DB, or the
-- live Supabase AFTER the polsia2 teardown) every object below creates cleanly.
--
-- REPLACE guard (robustness #1 — mediationplan.md Ground Truth, 2026-05-30): takyon
-- OWNS public.businesses. The live Supabase still hosts polsia2's differently-shaped
-- `businesses` (id-PK, owner_profile_id). `create table if not exists` would SILENTLY
-- bind takyon to that incompatible table instead of failing. So before touching
-- anything, fail loud if a non-takyon `businesses` is present and point the operator
-- at the one-time, backup-gated teardown. Trivial pass on a clean DB and on re-runs
-- (takyon's own businesses has owner_user_id, so the guard does not trip).
do $$
begin
    if to_regclass('public.businesses') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'businesses'
             and column_name  = 'owner_user_id'
       )
    then
        raise exception
            'public.businesses exists but is not the takyon shape (no owner_user_id). '
            'Run plugins/takyon/db/retire_polsia2_public.sql first (one-time, '
            'backup-gated polsia2 teardown), then re-apply takyon migrations. '
            'See mediationplan.md > Ground Truth (REPLACE decision, 2026-05-30).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

create extension if not exists citext;

-- Top-level Takyon users (operators/account owners). NOT product sub-users.
-- auth0_sub is the stable identity join key (Auth0 OIDC `sub`); email is mutable.
-- Stripe Connect payout fields are per-user, optional, and deferred: they are
-- null/'none' until the user opts in to withdraw and never gate onboarding.
create table if not exists users (
    id                         uuid primary key default gen_random_uuid(),
    auth0_sub                  citext not null unique,
    email                      citext,
    status                     text not null default 'active',
    created_at                 timestamptz not null default now(),
    stripe_connect_account_id  text,
    stripe_connect_status      text not null default 'none',
    payout_currency            text not null default 'usd',
    constraint users_status_chk
        check (status in ('active', 'suspended', 'closed')),
    constraint users_connect_status_chk
        check (stripe_connect_status in ('none', 'pending', 'active', 'restricted'))
);

-- The single opaque API key per user: the entire per-user boundary.
-- Platform-minted only; we store the SHA-256 hex hash and a non-secret prefix,
-- never the raw key. Rotation = insert a new row + set revoked_at on the old one,
-- preserving history for audit.
create table if not exists user_api_keys (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references users (id) on delete cascade,
    key_hash      text not null unique,
    prefix        text not null,
    created_at    timestamptz not null default now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz
);

-- Enforce exactly one active (non-revoked) key per user.
create unique index if not exists user_api_keys_one_active
    on user_api_keys (user_id)
    where revoked_at is null;

create index if not exists user_api_keys_user_idx
    on user_api_keys (user_id);

-- Business ownership is a first-class, enforced relation: every business is owned
-- by exactly one Takyon user. Later migrations extend this table; the spine fixes
-- identity + ownership + mode.
create table if not exists businesses (
    slug           text primary key,
    name           text not null,
    owner_user_id  uuid not null references users (id),
    mode           text not null default 'test',
    created_at     timestamptz not null default now(),
    constraint businesses_mode_chk check (mode in ('test', 'live'))
);

create index if not exists businesses_owner_idx
    on businesses (owner_user_id);
