-- 0005_app_identity.sql
-- Phase 5 (increment a): product SUB-USER identity, magic-link auth, and sessions.
--
-- These are the customers OF a business/product the Takyon user runs — NOT the
-- top-level Takyon operator (`users`, migration 0001). The whole product runtime is
-- scoped by `business_slug`: a sub-user belongs to exactly one business, and an email
-- is unique only within that business (the same person can be a customer of two
-- different businesses with the same email). This is the Postgres port of the SQLite
-- trunk's app_users / app_magic_links / app_sessions (core.py:3084-3122); the SQLite
-- product path is the predecessor, retired in Phase 8 (this is its successor, not a
-- second parallel authority).
--
-- Auth is magic-link only: opaque tokens are never stored in clear, only their
-- SHA-256 hex hash (matches the SQLite `_hash_token` so a ported app keeps working).
-- A magic link is single-use and short-lived (15 min by default); a session is a
-- 30-day bearer token. Email DELIVERY is a side effect owned by a higher layer (the
-- HTTP/tool surface), not this identity substrate — exactly as Phase 3 split
-- `billing.topup` (ledger state) from the Stripe call. The leaf mints and stores; the
-- layer above sends and records `provider_message_id`.
--
-- Idempotent DDL: safe to run repeatedly. Clean `public` only (local test DB, or live
-- Supabase AFTER the polsia2 teardown).
--
-- REPLACE guard (robustness #1 — mediationplan.md): mirror 0001-0004. app_users is
-- net-new to Postgres, but `create table if not exists` would SILENTLY bind to a
-- differently-shaped pre-existing table if one existed. takyon's app_users is
-- BUSINESS-scoped (it has business_slug); any non-takyon table of this name would be
-- auth-user-scoped and lack it. Fail loud in that case. app_users is the migration's
-- anchor (magic_links + sessions FK to it), so one guard on it covers the increment.
do $$
begin
    if to_regclass('public.app_users') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'app_users'
             and column_name  = 'business_slug'
       )
    then
        raise exception
            'public.app_users exists but is not the takyon shape (no business_slug). '
            'takyon sub-user identity is business-scoped; a differently-shaped table of '
            'this name is unexpected. Inspect and remove it before applying takyon '
            'migrations. See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

create extension if not exists citext;

-- One product sub-user (customer) per (business, email). email is citext so the
-- (business_slug, email) uniqueness and all lookups are case-insensitive without the
-- lower() dance the SQLite version needed. tier is business-defined (free/paid/...),
-- not constrained to a fixed set.
create table if not exists app_users (
    id            uuid primary key default gen_random_uuid(),
    business_slug text not null references businesses (slug) on delete cascade,
    email         citext not null,
    name          text,
    status        text not null default 'active'
                      check (status in ('active', 'suspended', 'closed')),
    tier          text not null default 'free' check (length(tier) > 0),
    metadata      jsonb not null default '{}'::jsonb,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    unique (business_slug, email)
);

-- Single-use, short-lived login tokens. token_hash is UNIQUE (the SHA-256 of the raw
-- token); used_at is stamped exactly once when redeemed. provider_message_id is left
-- NULL by the identity leaf and populated by the email-send layer above (NULL = not
-- sent / no provider record yet).
create table if not exists app_magic_links (
    id                  uuid primary key default gen_random_uuid(),
    business_slug       text not null references businesses (slug) on delete cascade,
    app_user_id         uuid not null references app_users (id) on delete cascade,
    email               citext not null,
    token_hash          text not null unique,
    purpose             text not null default 'login' check (length(purpose) > 0),
    expires_at          timestamptz not null,
    used_at             timestamptz,
    provider_message_id text,
    metadata            jsonb not null default '{}'::jsonb,
    created_at          timestamptz not null default now()
);

create index if not exists app_magic_links_user_idx
    on app_magic_links (business_slug, app_user_id);

-- 30-day bearer sessions. token_hash is the SHA-256 of the raw session token; a
-- session is valid while revoked_at is null and expires_at is in the future.
create table if not exists app_sessions (
    id            uuid primary key default gen_random_uuid(),
    business_slug text not null references businesses (slug) on delete cascade,
    app_user_id   uuid not null references app_users (id) on delete cascade,
    token_hash    text not null unique,
    expires_at    timestamptz not null,
    revoked_at    timestamptz,
    created_at    timestamptz not null default now()
);

create index if not exists app_sessions_user_idx
    on app_sessions (business_slug, app_user_id);
