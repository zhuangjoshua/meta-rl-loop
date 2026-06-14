-- 0025_canonical_revisions_and_live_pointer.sql
-- Canonical source revisions + immutable product build pointer.
--   * businesses.head_revision becomes the committed source pointer
--   * business_revisions records immutable canonical source revisions
--   * product_builds records immutable product build artifacts
--   * app_surface_contracts.live_build_id + probe fields record intended live state

alter table businesses
    add column if not exists head_revision bigint not null default 0;

create table if not exists business_revisions (
    business_slug    text not null references businesses(slug) on delete cascade,
    revision         bigint not null,
    parent_revision  bigint not null default 0,
    manifest_sha     text not null,
    actor            text not null,
    reason           text not null,
    created_at       timestamptz not null default now(),
    primary key (business_slug, revision)
);

create index if not exists business_revisions_business_created_idx
    on business_revisions (business_slug, created_at desc);

alter table app_surface_contracts
    add column if not exists live_build_id text;

alter table app_surface_contracts
    add column if not exists live_probe_status text not null default 'unknown';

alter table app_surface_contracts
    add column if not exists live_probe_detail text;

create table if not exists product_builds (
    build_id         text primary key,
    business_slug    text not null references businesses(slug) on delete cascade,
    source_revision  bigint not null,
    artifact_prefix  text not null,
    status           text not null default 'built',
    created_at       timestamptz not null default now()
);

create index if not exists product_builds_business_created_idx
    on product_builds (business_slug, created_at desc);
