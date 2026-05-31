-- 0009_app_gateway_keys.sql
-- Phase 5 (increment e): the PROJECT GATEWAY-KEY boundary — the net-new credential that lets
-- "generated app never holds provider key" (mediationplan Phase 5 ADD (b) + acceptance).
--
-- THE GAP THIS CLOSES (mediationplan Gate-1 finding, verified at source): the SQLite product
-- `/generate` path (app_api.py:395) calls Anthropic with the PLATFORM's shared key
-- (`_anthropic_key()`) directly — there is NO per-business gateway-key boundary in front of the
-- provider key. So any caller of the product AI route is one step from the raw platform key.
--
-- WHAT THIS ADDS: a per-business, INTERNALLY-minted capability. A business (its app runtime and the
-- product app it generates) receives a `tkg_…` gateway key; it presents that key to the internal AI
-- gateway, which resolves key -> business_slug, applies policy (0004) + the product budget gate
-- (0007), calls the SHARED provider key SERVER-SIDE, and settles. The generated app therefore holds
-- ONLY its own gateway key and never the platform provider key (the Phase 5 acceptance). Minting a
-- hash needs no external account — see mediationplan Gate 2 (Phase 5 = no new credential).
--
-- SHAPE — deliberately mirrors `user_api_keys` (0001:63) so the at-rest discipline is identical:
-- store only `key_hash` (SHA-256 hex) + a non-secret `prefix` for display/lookup; the raw key is
-- shown once at mint and is unrecoverable. The leaf reuses `user_api_keys.hash_api_key` (the hashing
-- is prefix-agnostic) but mints in a DISTINCT keyspace (`tkg_`, disjoint from the user `tk_`
-- keyspace) so a per-USER key and a per-BUSINESS gateway key can never be confused or cross-resolve.
--
-- DELIBERATE DIVERGENCE from `user_api_keys`: NO one-active-per-scope unique index. A user has
-- exactly one active key (the whole per-user boundary); a business may legitimately hold SEVERAL
-- active gateway keys at once (the app runtime + the generated app, or an overlapping rotation where
-- the old key keeps the deployed app working until cutover). Rotation here is therefore mint-new +
-- revoke-old as separate steps, not the atomic single-row swap `rotate_api_key` does.
--
-- REPLACE guard (robustness #1 — mediationplan.md): mirror 0001-0008. `app_gateway_keys` is net-new
-- to BOTH the SQLite trunk (no predecessor table — confirmed by grep) and Postgres, but
-- `create table if not exists` would SILENTLY bind to a differently-shaped pre-existing table if one
-- existed. Distinguishing takyon-shape column = `business_slug` (this is the business-scoped gateway
-- credential, not some unrelated same-named table). Fail loud if a same-named table lacks it.
do $$
begin
    if to_regclass('public.app_gateway_keys') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'app_gateway_keys'
             and column_name  = 'business_slug'
       )
    then
        raise exception
            'public.app_gateway_keys exists but is not the takyon shape (no business_slug). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

-- A project gateway key. business_slug CASCADE: a deleted business takes its gateway keys with it
-- (a resolvable key therefore always points at a live business — the resolver needs no existence
-- join). key_hash UNIQUE is both the dedup key and the hot resolve index. prefix is the non-secret
-- lookup/display hint. Revocation is soft (revoked_at) so a revoked key stays for audit.
create table if not exists app_gateway_keys (
    id            uuid primary key default gen_random_uuid(),
    business_slug text not null references businesses (slug) on delete cascade,
    key_hash      text not null unique check (length(key_hash) > 0),
    prefix        text not null check (length(prefix) > 0),
    revoked_at    timestamptz,
    created_at    timestamptz not null default now()
);

-- Listing / revoking a business's keys looks up by business_slug; the resolve hot path is served by
-- the UNIQUE(key_hash) index above.
create index if not exists app_gateway_keys_business_idx
    on app_gateway_keys (business_slug, created_at desc);
