-- 0001_identity_spine.sql
-- Takyon control plane (Postgres / Supabase target) — P1 identity spine.
--
-- Creates the top-level Takyon operator accounts, the single opaque per-user API
-- key, and first-class business ownership. This is the canonical source of truth
-- for the eventual Postgres control plane; the current SQLite control plane in
-- plugins/takyon/core.py (TakyonStore) is being superseded per mediationplan.md.
--
-- Idempotent: safe to run repeatedly. Greenfield: no backfill — on the Postgres
-- plane there is no pre-existing ownerless business data.

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
