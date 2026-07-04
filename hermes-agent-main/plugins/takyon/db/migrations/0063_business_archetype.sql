-- 0063_business_archetype.sql
-- App Store rail + general-apps manifest (readmodular.md §1.2; general-apps-plan.md §1) — the
-- per-business ARCHETYPE record: manifest key #3, the `app | shopify | saas` toggle.
--
-- ONE additive, non-destructive change, OPERATOR/CEO-plane (a business declaration, not subuser
-- auth), mirroring 0062's money_shape column exactly:
--
--   `businesses.archetype` — one of 'web_saas' (the default), 'mobile_app', 'shopify_commerce'.
--   NOT NULL DEFAULT 'web_saas', so the backfill is the default itself: every existing row reads
--   as web_saas = today's behavior byte-for-byte, no data migration, no destructive change. An
--   archetype is a named manifest PRESET (plugins/takyon/archetypes.py), never a code path.
--
-- SUBUSER-SECURITY INVARIANT (identical to 0062's money_shape): nothing here touches subuser auth,
-- entitlements, sessions, or the money ledgers. The column is additive and is deliberately NOT
-- added to the app-runtime business view (`takyon_app_runtime_business`), so the subuser plane
-- cannot read it — the App Store pipeline has zero subuser surface. Only the operator/runtime/
-- safebox/migration roles get the column-level UPDATE grant (INSERT is table-level, not
-- column-restricted, so create needs no extra grant — verified against 0038/0044).

begin;

-- Additive NOT NULL column with a default. Unlike money_shape (nullable, NULL=default), archetype
-- is NOT NULL DEFAULT 'web_saas': the default IS the backfill, and every code path can read a
-- concrete value. A CHECK pins the known set (kept in sync with archetypes.ARCHETYPES).
alter table public.businesses
    add column if not exists archetype text not null default 'web_saas';

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'businesses_archetype_chk'
          and conrelid = 'public.businesses'::regclass
    ) then
        alter table public.businesses
            add constraint businesses_archetype_chk
            check (archetype in ('web_saas', 'mobile_app', 'shopify_commerce'));
    end if;
end $$;

-- The operator-plane writers hold COLUMN-level UPDATE on businesses (0038/0044 revoked table-level
-- UPDATE and granted an enumerated column list). Those enumerations run BEFORE this file in a
-- fresh lexical replay, so the new column must grant its own UPDATE here — otherwise the gated
-- archetype change (archetypes.set_archetype) is permission-denied until the NEXT full replay.
-- INSERT is table-level (no column enumeration exists for businesses INSERT), so create-time
-- archetype writes need no grant here.
do $$
declare
    wr text;
begin
    foreach wr in array array['takyon_runtime', 'takyon_operator_runtime'] loop
        if exists (select 1 from pg_roles where rolname = wr) then
            execute format('grant update (archetype) on public.businesses to %I', wr);
        end if;
    end loop;
end $$;

commit;
