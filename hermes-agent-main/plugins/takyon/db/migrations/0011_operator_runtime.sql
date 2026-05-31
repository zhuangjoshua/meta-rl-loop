-- 0011_operator_runtime.sql
-- Phase 8 (kill SQLite): port the SQLite operator runtime tables to Postgres (mediationplan.md >
-- Phase 8 gate finding, 2026-05-31). This is the storage half of the big-bang cutover: it gives the
-- 10 SQLite-only operator tables a Postgres home and enriches `businesses` to the operator shape, so
-- the next increment (the Postgres-backed `TakyonStore` seam) has real tables to read/write. It is
-- ADDITIVE and guarded — applying it does NOT flip serving and does NOT touch the worker plane.
--
-- SCOPE (verified Gate 1, repo + live backend): `core.py:_init_db` is the authoritative 22-table
-- SQLite operator schema. 12 already have a PG home (`businesses` 0001; the eleven `app_*` product
-- tables 0005-0008). The 10 SQLite-only tables ported here:
--   workspaces, agent_runs, ledger_entries, control_states, events, conversation_threads,
--   conversation_messages, idempotency_keys, app_surface_contracts, and the operator work-request
--   table (the SQLite `jobs`, ISOLATED here as `business_work_requests` — see below).
--
-- WHY business_work_requests (ISOLATE, not RECONCILE): the SQLite `jobs` table is a *work-request
-- record* (`business_enqueue_job`, INSERT at core.py:5518; only counted/listed/GC'd — core.py never
-- drains it to execute). That is categorically different from the 0010 worker-plane `jobs` *execution
-- queue* (uuid PK, idempotency_key UNIQUE, reserved_billing_entry_id, FOR UPDATE SKIP LOCKED). Folding
-- the request-record into the execution queue would pollute the queue the worker drains; isolating it
-- under a distinct name preserves BOTH exact semantics. (Consolidating work-requests onto the worker
-- plane is a later BEHAVIORAL step, not this storage port.) The live `public.jobs` is already the
-- takyon 0010 shape (0 rows) and is left untouched.
--
-- WHY text timestamps / text JSON (deliberate, differs from 0001-0010): the SQLite operator store
-- stores created_at/updated_at/received_at as ISO-8601 strings (Python `_now()`) and `*_json` columns
-- as `json.dumps`'d TEXT, and reads/compares them as strings (`WHERE created_at < ?` GC core.py:5803;
-- pulse windows core.py:3976+; `_row_to_dict` `json.loads`). The ONLY reader of these 10 tables is the
-- ported operator store, which speaks ISO strings — so `text` is the exact 1:1 port with no coercion.
-- 0001-0010 use timestamptz/jsonb because their readers are psycopg leaf modules written for
-- datetimes/dicts. Different access code for different tables — the smallest faithful port, NOT a
-- parallel system. `ledger_entries.amount` (SQLite REAL) → `double precision` (PG float8 = SQLite REAL).
--
-- REPLACE guard (robustness #1 — mediationplan.md): mirror 0001-0010. `create table if not exists`
-- would SILENTLY bind to a differently-shaped pre-existing table. Each table below is preceded by a
-- fail-loud guard keyed on a takyon-distinguishing column, so a non-takyon same-named table RAISES
-- instead of binding. Live introspection (2026-05-31) found three polsia2-shaped collisions —
-- `agent_runs` (70 rows; no `scope`), `events` (1274 rows; has `kind`, no `event_type`),
-- `idempotency_keys` (0 rows; has `response`, no `operation_hash`) — which `retire_polsia2_public.sql`
-- now drops (guarded) BEFORE this migration; the guards here are the fail-loud backstop if it wasn't.
-- The other seven names were absent from live `public` → clean install. Idempotent: on a takyon-shaped
-- DB every guard passes and every `create ... if not exists` / `add column if not exists` is a no-op.

-- ── businesses: ENRICH to the operator shape ───────────────────────────────────────────────────────
-- Same entity as 0001's slim row (slug PK, name, owner_user_id, mode, created_at) — stays ONE table.
-- The operator runtime needs goal/status/work_focus/budget_json/metadata_json/updated_at. owner_user_id
-- stays NOT NULL (the store seam resolves/seeds an owner); created_at/updated_at stay timestamptz (0001
-- consumers expect it) and the store seam coerces datetime↔ISO. 0001's identity guard already protects
-- this table, so no extra guard here — these are purely additive columns/constraints/indexes.
alter table businesses add column if not exists goal          text        not null default '';
alter table businesses add column if not exists status        text        not null default 'active';
alter table businesses add column if not exists work_focus    text        not null default 'all';
alter table businesses add column if not exists budget_json   text;
alter table businesses add column if not exists metadata_json text;
alter table businesses add column if not exists updated_at    timestamptz not null default now();

-- work_focus is a closed enum in the SQLite store (_migrate_db: all|marketing|product). Add the CHECK
-- once, idempotently (PG has no `add constraint if not exists`). status is intentionally NOT checked —
-- the store uses open-ended status values ('active', and lifecycle states) and SQLite never constrained it.
do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'businesses_work_focus_chk') then
        alter table businesses
            add constraint businesses_work_focus_chk check (work_focus in ('all', 'marketing', 'product'));
    end if;
end $$;

create index if not exists businesses_mode_idx       on businesses (mode, updated_at desc);
create index if not exists businesses_work_focus_idx on businesses (work_focus, updated_at desc);

-- ── workspaces ─────────────────────────────────────────────────────────────────────────────────────
do $$
begin
    if to_regclass('public.workspaces') is not null
       and not exists (select 1 from information_schema.columns
                       where table_schema='public' and table_name='workspaces' and column_name='budget_json')
    then
        raise exception
            'public.workspaces exists but is not the takyon shape (no budget_json). '
            'Inspect and remove it before applying takyon migrations. See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;
create table if not exists workspaces (
    id            text not null primary key,
    business_slug text not null references businesses (slug) on delete cascade,
    path          text not null,
    kind          text not null default 'workspace',
    status        text not null default 'active',
    budget_json   text,
    metadata_json text,
    created_at    text not null,
    updated_at    text not null,
    unique (business_slug, path)
);

-- ── agent_runs (retire polsia2's 70-row analog FIRST) ───────────────────────────────────────────────
do $$
begin
    if to_regclass('public.agent_runs') is not null
       and not exists (select 1 from information_schema.columns
                       where table_schema='public' and table_name='agent_runs' and column_name='scope')
    then
        raise exception
            'public.agent_runs exists but is not the takyon shape (no scope) — polsia2 leftover. '
            'Run plugins/takyon/db/retire_polsia2_public.sql first, then re-apply. See mediationplan.md > Phase 8 gate finding.'
            using errcode = 'feature_not_supported';
    end if;
end $$;
create table if not exists agent_runs (
    id          text not null primary key,
    scope       text not null,
    parent_id   text,
    status      text not null,
    prompt      text,
    result_json text,
    created_at  text not null,
    updated_at  text not null
);

-- ── ledger_entries (operator per-business internal allocate/spend budget ledger) ───────────────────
-- Distinct from flow-A billing_entries, flow-B custody_entries (real money), and app_budgets (sub-user
-- microUSD cap). No PG table covers it. Absent from live public → clean install. amount = float8.
do $$
begin
    if to_regclass('public.ledger_entries') is not null
       and not exists (select 1 from information_schema.columns
                       where table_schema='public' and table_name='ledger_entries' and column_name='scope')
    then
        raise exception
            'public.ledger_entries exists but is not the takyon shape (no scope). '
            'Inspect and remove it before applying takyon migrations. See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;
create table if not exists ledger_entries (
    id            text not null primary key,
    scope         text not null,
    business_slug text,
    amount        double precision not null,
    currency      text not null default 'USD',
    kind          text not null,
    status        text not null,
    payload_json  text,
    created_at    text not null
);

-- ── control_states (pause/kill safety rail) ─────────────────────────────────────────────────────────
do $$
begin
    if to_regclass('public.control_states') is not null
       and not exists (select 1 from information_schema.columns
                       where table_schema='public' and table_name='control_states' and column_name='actor')
    then
        raise exception
            'public.control_states exists but is not the takyon shape (no actor). '
            'Inspect and remove it before applying takyon migrations. See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;
create table if not exists control_states (
    scope      text not null primary key,
    state      text not null,
    reason     text not null default '',
    actor      text not null default '',
    updated_at text not null
);

-- ── events (retire polsia2's 1274-row analog FIRST) ─────────────────────────────────────────────────
do $$
begin
    if to_regclass('public.events') is not null
       and not exists (select 1 from information_schema.columns
                       where table_schema='public' and table_name='events' and column_name='event_type')
    then
        raise exception
            'public.events exists but is not the takyon shape (no event_type) — polsia2 leftover. '
            'Run plugins/takyon/db/retire_polsia2_public.sql first, then re-apply. See mediationplan.md > Phase 8 gate finding.'
            using errcode = 'feature_not_supported';
    end if;
end $$;
create table if not exists events (
    id            text not null primary key,
    scope         text not null,
    business_slug text,
    event_type    text not null,
    payload_json  text,
    created_at    text not null
);

-- ── conversation_threads ────────────────────────────────────────────────────────────────────────────
do $$
begin
    if to_regclass('public.conversation_threads') is not null
       and not exists (select 1 from information_schema.columns
                       where table_schema='public' and table_name='conversation_threads' and column_name='external_id')
    then
        raise exception
            'public.conversation_threads exists but is not the takyon shape (no external_id). '
            'Inspect and remove it before applying takyon migrations. See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;
create table if not exists conversation_threads (
    id            text not null primary key,
    business_slug text not null references businesses (slug) on delete cascade,
    source        text not null,
    external_id   text not null,
    title         text not null,
    url           text,
    status        text not null default 'active',
    created_at    text not null,
    updated_at    text not null,
    unique (business_slug, source, external_id)
);
create index if not exists conversation_threads_business_status_idx
    on conversation_threads (business_slug, status, updated_at desc);

-- ── conversation_messages (after conversation_threads — FK target) ──────────────────────────────────
do $$
begin
    if to_regclass('public.conversation_messages') is not null
       and not exists (select 1 from information_schema.columns
                       where table_schema='public' and table_name='conversation_messages' and column_name='direction')
    then
        raise exception
            'public.conversation_messages exists but is not the takyon shape (no direction). '
            'Inspect and remove it before applying takyon migrations. See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;
create table if not exists conversation_messages (
    id            text not null primary key,
    business_slug text not null references businesses (slug) on delete cascade,
    thread_id     text not null references conversation_threads (id) on delete cascade,
    source        text not null,
    external_id   text not null,
    direction     text not null,
    author_label  text not null default '',
    body          text not null default '',
    status        text not null default 'needs_response',
    received_at   text not null,
    created_at    text not null,
    updated_at    text not null,
    unique (business_slug, source, external_id)
);
create index if not exists conversation_messages_business_status_idx
    on conversation_messages (business_slug, status, received_at desc);

-- ── idempotency_keys (retire polsia2's analog FIRST) ────────────────────────────────────────────────
-- Operator-level generic op-dedup (key → result). Distinct from jobs.idempotency_key (0010, the queue's
-- at-least-once dedup). polsia2's analog has `response`/`business_id`, no `operation_hash`.
do $$
begin
    if to_regclass('public.idempotency_keys') is not null
       and not exists (select 1 from information_schema.columns
                       where table_schema='public' and table_name='idempotency_keys' and column_name='operation_hash')
    then
        raise exception
            'public.idempotency_keys exists but is not the takyon shape (no operation_hash) — polsia2 leftover. '
            'Run plugins/takyon/db/retire_polsia2_public.sql first, then re-apply. See mediationplan.md > Phase 8 gate finding.'
            using errcode = 'feature_not_supported';
    end if;
end $$;
create table if not exists idempotency_keys (
    key            text not null primary key,
    operation_hash text not null,
    result_json    text not null,
    created_at     text not null
);

-- ── app_surface_contracts (operator-owned product UI contract; NOT in 0005-0008) ───────────────────
-- Full shape from core.py:3061-3085 (the base CREATE already carries every publish_* column the SQLite
-- _migrate_db would otherwise back-fill, so a fresh table needs no follow-up ALTERs).
do $$
begin
    if to_regclass('public.app_surface_contracts') is not null
       and not exists (select 1 from information_schema.columns
                       where table_schema='public' and table_name='app_surface_contracts' and column_name='design_brief_path')
    then
        raise exception
            'public.app_surface_contracts exists but is not the takyon shape (no design_brief_path). '
            'Inspect and remove it before applying takyon migrations. See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;
create table if not exists app_surface_contracts (
    business_slug        text not null primary key references businesses (slug) on delete cascade,
    status               text not null default 'draft',
    design_brief_path    text not null default 'product/design-brief.md',
    source_path          text,
    runtime_api_base     text,
    runtime_features_json text,
    routes_json          text,
    theme_json           text,
    constraints_json     text,
    publish_target       text,
    publish_policy       text not null default 'publish_after_verify',
    mode_behavior        text not null default 'test_mode_publishes_product_surface',
    done_gate            text not null default 'business_verify_product_surface:verified_and_published_or_exact_blocker',
    public_url           text,
    publish_status       text not null default 'not_published',
    published_at         text,
    publish_receipt_path text,
    publish_blocker      text,
    notes                text,
    metadata_json        text,
    created_at           text not null,
    updated_at           text not null
);

-- ── business_work_requests (the operator `jobs` work-request record — ISOLATED from worker-plane jobs) ─
do $$
begin
    if to_regclass('public.business_work_requests') is not null
       and not exists (select 1 from information_schema.columns
                       where table_schema='public' and table_name='business_work_requests' and column_name='scope')
    then
        raise exception
            'public.business_work_requests exists but is not the takyon shape (no scope). '
            'Inspect and remove it before applying takyon migrations. See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;
create table if not exists business_work_requests (
    id            text not null primary key,
    scope         text not null,
    business_slug text,
    kind          text not null,
    status        text not null default 'queued',
    payload_json  text,
    created_at    text not null,
    updated_at    text not null
);
