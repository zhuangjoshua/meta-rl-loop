-- Canonical Takyon migration role topology.
-- Run with a privileged database owner before replaying db/migrations/*.sql as takyon_migration.
-- Public schema scope is deliberate; do not use REASSIGN OWNED BY postgres on Supabase.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'takyon_migration') then
    create role takyon_migration login noinherit nosuperuser nobypassrls;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'takyon_app') then
    create role takyon_app nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'safebox') then
    create role safebox nologin nosuperuser nobypassrls;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'takyon_app_runtime') then
    create role takyon_app_runtime login noinherit nosuperuser nobypassrls;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'takyon_operator_runtime') then
    create role takyon_operator_runtime login noinherit nosuperuser nobypassrls;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'takyon_safebox_authority') then
    create role takyon_safebox_authority login noinherit nosuperuser nobypassrls;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'takyon_runtime') then
    create role takyon_runtime nologin;
  end if;
end $$;

grant usage, create on schema public to takyon_migration;

grant takyon_app to takyon_migration with inherit false, set true;
grant takyon_app to takyon_runtime with inherit false, set true;

grant takyon_app to takyon_migration with admin option;
grant takyon_app_runtime to takyon_migration with admin option;
grant takyon_operator_runtime to takyon_migration with admin option;
grant takyon_safebox_authority to takyon_migration with admin option;
grant takyon_runtime to takyon_migration with admin option;

do $$
declare rel record;
begin
  for rel in
    select c.relkind, c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    join pg_roles r on r.oid = c.relowner
    where n.nspname = 'public'
      and c.relkind in ('r', 'p', 'f')
      and r.rolname <> 'takyon_migration'
      and not exists (
        select 1
        from pg_depend d
        where d.classid = 'pg_class'::regclass
          and d.objid = c.oid
          and d.deptype = 'e'
      )
  loop
    if rel.relkind = 'f' then
      execute format('alter foreign table public.%I owner to takyon_migration', rel.relname);
    else
      execute format('alter table public.%I owner to takyon_migration', rel.relname);
    end if;
  end loop;
end $$;

do $$
declare seq record;
begin
  for seq in
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    join pg_roles r on r.oid = c.relowner
    where n.nspname = 'public'
      and c.relkind = 'S'
      and r.rolname <> 'takyon_migration'
      and not exists (
        select 1
        from pg_depend d
        where d.classid = 'pg_class'::regclass
          and d.objid = c.oid
          and d.deptype = 'e'
      )
  loop
    execute format('alter sequence public.%I owner to takyon_migration', seq.relname);
  end loop;
end $$;

do $$
declare v record;
begin
  for v in
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    join pg_roles r on r.oid = c.relowner
    where n.nspname = 'public'
      and c.relkind = 'v'
      and r.rolname <> 'takyon_migration'
      and not exists (
        select 1
        from pg_depend d
        where d.classid = 'pg_class'::regclass
          and d.objid = c.oid
          and d.deptype = 'e'
      )
  loop
    execute format('alter view public.%I owner to takyon_migration', v.relname);
  end loop;
end $$;

do $$
declare mv record;
begin
  for mv in
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    join pg_roles r on r.oid = c.relowner
    where n.nspname = 'public'
      and c.relkind = 'm'
      and r.rolname <> 'takyon_migration'
      and not exists (
        select 1
        from pg_depend d
        where d.classid = 'pg_class'::regclass
          and d.objid = c.oid
          and d.deptype = 'e'
      )
  loop
    execute format('alter materialized view public.%I owner to takyon_migration', mv.relname);
  end loop;
end $$;

do $$
declare routine record;
begin
  for routine in
    select p.oid::regprocedure as sig
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    join pg_roles r on r.oid = p.proowner
    where n.nspname = 'public'
      and r.rolname <> 'takyon_migration'
      and not exists (
        select 1
        from pg_depend d
        where d.classid = 'pg_proc'::regclass
          and d.objid = p.oid
          and d.deptype = 'e'
      )
  loop
    execute format('alter routine %s owner to takyon_migration', routine.sig);
  end loop;
end $$;

do $$
declare typ record;
begin
  for typ in
    select t.typname
    from pg_type t
    join pg_namespace n on n.oid = t.typnamespace
    join pg_roles r on r.oid = t.typowner
    where n.nspname = 'public'
      and t.typcategory <> 'A'
      and t.typrelid = 0
      and r.rolname <> 'takyon_migration'
      and not exists (
        select 1
        from pg_depend d
        where d.classid = 'pg_type'::regclass
          and d.objid = t.oid
          and d.deptype = 'e'
      )
  loop
    execute format('alter type public.%I owner to takyon_migration', typ.typname);
  end loop;
end $$;
