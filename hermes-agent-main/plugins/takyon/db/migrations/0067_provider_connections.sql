-- 0067_provider_connections.sql
-- Egress/connections rail (general-apps-plan delta 6): the credentialed-egress connection store.
-- Build contract + full threat model: egress-rail-build-spec.md (design + 2 hostile-subuser
-- red-team passes). Additive, non-destructive, DDL style copied from 0062.
--
-- ONE new table for a NEW concern: a business-scoped third-party credential the safebox attaches
-- to a single outbound request on the connection's OWN host. `app_connections` stays the social
-- like/pass/block rail and carries NO credentials — this is a separate leaf per one-table-per-
-- concern.
--
-- HOSTILE-SUBUSER SECURITY (subusers author the Deno action code that reaches this via the
-- safebox /v1/egress route):
--   * The subuser/app-runtime role has ZERO access to this table (REVOKE ALL) — the whole rail is
--     server-side; the action never reads a connection, never sees a credential.
--   * secret_ciphertext / secret_nonce are readable ONLY by takyon_safebox_authority (the safebox
--     process). The operator/runtime plane gets COLUMN-level grants on the METADATA columns only
--     (create/list/revoke a connection) — it can never read the sealed secret. Column grants alone
--     enforce the ciphertext wall (same rationale as 0062 operator_approvals: no SECURITY DEFINER
--     needed because the untrusted plane has no access at all and the safebox reads directly).
--   * The seal key TAKYON_CONNECTION_SEAL_KEY is a safebox-process secret, categorically
--     non-egress over /v1/env (core._SAFEBOX_SELF_AUTHORITY_SECRETS) — it is NOT in this migration.
--   * Cross-tenant is impossible: the safebox always resolves WHERE business_slug = the
--     HMAC-signed capability scope's business_slug (never a caller-supplied slug) AND
--     connection_slug AND status='active'.

begin;

create table if not exists public.provider_connections (
    id                  uuid primary key default gen_random_uuid(),
    business_slug       text not null references public.businesses(slug) on delete cascade,
    connection_slug     text not null,                              -- business-facing handle, e.g. 'stripe-live'
    provider_kind       text not null,                             -- free label, e.g. 'stripe','github'
    allowed_host        text not null,                             -- the single host the credential may reach (lowercased, no scheme)
    allowed_path_prefix text,                                       -- optional path allowlist prefix
    allowed_methods     text[] not null default '{GET,POST}',
    placement           jsonb  not null default '{}'::jsonb,        -- {type: header|query|basic, name}
    secret_ciphertext   bytea,                                     -- AEAD-sealed under the safebox seal key; safebox-only column
    secret_nonce        bytea,                                     -- safebox-only column
    secret_fingerprint  text,                                       -- sha256(plaintext) for rotation/audit — never the secret
    scope               text not null default 'business'
                        check (scope in ('business', 'per_customer')),
    status              text not null default 'pending'
                        check (status in ('pending', 'active', 'revoked')),
    approval_id         uuid references public.operator_approvals(id),
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    metadata_json       jsonb not null default '{}'::jsonb,
    -- Idempotent business-facing handle: one connection per (business, slug).
    unique (business_slug, connection_slug)
);
create index if not exists provider_connections_business_status_idx
    on public.provider_connections (business_slug, status);

-- ---------------------------------------------------------------------------
-- GRANTS — the ciphertext boundary. Subuser/app runtime EXPLICITLY denied (zero access). The
-- operator/runtime plane gets COLUMN-level grants on metadata ONLY (never the sealed secret);
-- the safebox authority is the ONLY role that reads/writes the secret columns.
-- ---------------------------------------------------------------------------
do $$
declare
    wr text;
    metacols text := 'business_slug, connection_slug, provider_kind, allowed_host, '
                  || 'allowed_path_prefix, allowed_methods, placement, secret_fingerprint, '
                  || 'scope, status, approval_id, created_at, updated_at, metadata_json, id';
begin
    revoke all on table public.provider_connections from public;
    if exists (select 1 from pg_roles where rolname = 'takyon_app_runtime') then
        revoke all on table public.provider_connections from takyon_app_runtime;
    end if;
    if exists (select 1 from pg_roles where rolname = 'takyon_app') then
        revoke all on table public.provider_connections from takyon_app;
    end if;

    -- Migration owner + safebox authority: full access (safebox reads/writes the sealed secret).
    foreach wr in array array['takyon_migration', 'takyon_safebox_authority'] loop
        if exists (select 1 from pg_roles where rolname = wr) then
            execute format('grant select, insert, update, delete on table public.provider_connections to %I', wr);
        end if;
    end loop;

    -- Operator/runtime plane: create/list/revoke a connection via METADATA columns only. NO grant
    -- on secret_ciphertext / secret_nonce, so the CEO/operator plane can never read the sealed
    -- secret. SELECT is column-scoped; INSERT/UPDATE are column-scoped to the same metadata set
    -- (the secret columns are written by the safebox deposit route under takyon_safebox_authority).
    foreach wr in array array['takyon_operator_runtime', 'takyon_runtime'] loop
        if exists (select 1 from pg_roles where rolname = wr) then
            execute format('grant select (%s) on table public.provider_connections to %I', metacols, wr);
            execute format('grant insert (%s) on table public.provider_connections to %I', metacols, wr);
            execute format('grant update (%s) on table public.provider_connections to %I', metacols, wr);
            execute format('grant delete on table public.provider_connections to %I', wr);
        end if;
    end loop;
end $$;

-- No RLS: the boundary here is GRANTS, exactly like operator_approvals (0062) — the subuser
-- plane has zero table privilege, the operator plane cannot read the ciphertext columns, and the
-- safebox resolves every row by the HMAC-signed capability scope's business_slug (never a
-- caller slug), so cross-tenant is closed at the query. Policyless RLS would instead deny the
-- legitimate operator/runtime metadata reads (no policy = no rows for non-BYPASSRLS roles), so it
-- is deliberately omitted — matching the 0062 approval rail's grants-only model.

commit;
