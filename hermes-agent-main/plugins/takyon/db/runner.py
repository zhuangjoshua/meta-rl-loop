"""Idempotent migration runner — the single production path that brings a Postgres database to the
current takyon schema.

``run_migrations(conn)`` applies every file in ``db/migrations/*.sql`` in lexical order (0001…,
0002…, …). It is deliberately the SAME logic the test fixtures use (``tests/plugins/conftest.py``
delegates to it), so the schema a test runs against and the schema production runs against come from
ONE definition in ONE order — there is no second, silently-drifting copy of "apply the migrations".

Idempotent by construction: every migration uses ``create table if not exists`` / ``create index if
not exists`` / guarded ``do $$ … $$`` REPLACE blocks, so running this on an already-current database
is a safe no-op and is the intended "bring the DB to current" operation. There is no down-migration
and no partial state to reconcile; a genuinely wrong-shaped pre-existing table makes a guard raise
loudly (robustness #1 — mediationplan.md) rather than bind silently.

Pure leaf: it takes a psycopg ``conn`` and never opens or closes one itself — the caller owns the
connection and its mode. Both the test fixtures and the runtime host pass an autocommit connection,
matching how every leaf in this package is exercised.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

# The migrations live next to this module. Single source of the path for BOTH production and tests.
DB_DIR = Path(__file__).resolve().parent
MIGRATIONS_DIR = DB_DIR / "migrations"
TOPOLOGY_SQL = DB_DIR / "topology.sql"

MIGRATION_ROLE = "takyon_migration"
_REQUIRED_ROLE_CREATE_SQL = {
    MIGRATION_ROLE: "CREATE ROLE takyon_migration LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS;",
    "safebox": "CREATE ROLE safebox NOLOGIN NOSUPERUSER NOBYPASSRLS;",
    "takyon_app": "CREATE ROLE takyon_app NOLOGIN NOSUPERUSER NOBYPASSRLS;",
    "takyon_app_runtime": "CREATE ROLE takyon_app_runtime LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS;",
    "takyon_operator_runtime": "CREATE ROLE takyon_operator_runtime LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS;",
    "takyon_safebox_authority": "CREATE ROLE takyon_safebox_authority LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS;",
    "takyon_runtime": "CREATE ROLE takyon_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS;",
}
_REQUIRED_ADMIN_MEMBERSHIPS = (
    "takyon_app",
    "takyon_app_runtime",
    "takyon_operator_runtime",
    "takyon_safebox_authority",
    "takyon_runtime",
)
_REQUIRED_SET_MEMBERSHIPS = (
    ("takyon_app", MIGRATION_ROLE),
)


class MigrationTopologyError(RuntimeError):
    """Raised when the migration role cannot replay the tracked migrations safely."""


def migration_files() -> list[Path]:
    """Every migration file in apply order (lexical by name — the 0001/0002/… prefixes ARE the
    order). Scoped to ``migrations/`` so anything kept deliberately outside it (e.g. the
    manually-gated ``retire_polsia2_public.sql`` teardown) is never swept into a normal run."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def topology_sql_path() -> Path:
    """Canonical privileged bootstrap/repair SQL for the migration role topology."""
    return TOPOLOGY_SQL


def assert_migration_topology(conn) -> None:
    """Validate the durable topology required before replaying migrations as takyon_migration.

    Superuser/test-admin connections deliberately skip this assertion so fixture setup can create the
    topology before switching into a real non-superuser migration role. Production migration runs use
    ``takyon_migration`` and get a complete list of missing fixes before any migration file executes.
    """
    current_user, is_superuser = _current_user(conn)
    if current_user != MIGRATION_ROLE or is_superuser:
        return

    fixes: list[str] = []
    fixes.extend(_missing_required_roles(conn))
    fixes.extend(_missing_admin_memberships(conn))
    fixes.extend(_missing_set_memberships(conn))
    fixes.extend(_public_owner_fixes(conn))

    if fixes:
        unique = sorted(dict.fromkeys(fixes))
        raise MigrationTopologyError(
            "Migration topology is not ready for takyon_migration. Run these as postgres or another "
            "privileged database owner before retrying:\n"
            + "\n".join(f"- {fix}" for fix in unique)
        )


def run_migrations(conn) -> list[str]:
    """Apply every migration in order against ``conn``; return the applied filenames (in order).

    One ``conn.execute(<file text>)`` per file — exactly what the test fixtures have always done — so
    this runner and the test harness can never disagree about what "the schema" is. Idempotent and
    safe to call on an already-current database. The caller owns the connection/transaction mode.
    """
    assert_migration_topology(conn)

    applied: list[str] = []
    for sql_path in migration_files():
        conn.execute(sql_path.read_text())
        applied.append(sql_path.name)
    return applied


def _current_user(conn) -> tuple[str, bool]:
    row = conn.execute(
        """
        select current_user,
               coalesce((select rolsuper from pg_roles where rolname = current_user), false)
        """
    ).fetchone()
    if row is None:
        return "", False
    return str(_cell(row, 0)), bool(_cell(row, 1))


def _missing_required_roles(conn) -> list[str]:
    required = tuple(_REQUIRED_ROLE_CREATE_SQL)
    values_sql = ", ".join(f"('{role}')" for role in required)
    rows = conn.execute(
        f"""
        with required(rolname) as (values {values_sql})
        select required.rolname
        from required
        left join pg_roles r on r.rolname = required.rolname
        where r.oid is null
        order by required.rolname
        """
    ).fetchall()
    return [_REQUIRED_ROLE_CREATE_SQL[str(_cell(row, 0))] for row in rows]


def _missing_admin_memberships(conn) -> list[str]:
    values_sql = ", ".join(f"('{role}')" for role in _REQUIRED_ADMIN_MEMBERSHIPS)
    rows = conn.execute(
        f"""
        with required(parent_role) as (values {values_sql})
        select required.parent_role
        from required
        join pg_roles parent_role on parent_role.rolname = required.parent_role
        join pg_roles member_role on member_role.rolname = '{MIGRATION_ROLE}'
        left join pg_auth_members membership
          on membership.roleid = parent_role.oid
         and membership.member = member_role.oid
         and membership.admin_option
        where membership.roleid is null
        order by required.parent_role
        """
    ).fetchall()
    return [f"GRANT {_cell(row, 0)} TO {MIGRATION_ROLE} WITH ADMIN OPTION;" for row in rows]


def _missing_set_memberships(conn) -> list[str]:
    values_sql = ", ".join(
        f"('{parent_role}', '{member_role}')" for parent_role, member_role in _REQUIRED_SET_MEMBERSHIPS
    )
    rows = conn.execute(
        f"""
        with required(parent_role, member_role) as (values {values_sql})
        select required.parent_role, required.member_role
        from required
        join pg_roles parent_role on parent_role.rolname = required.parent_role
        join pg_roles member_role on member_role.rolname = required.member_role
        left join pg_auth_members membership
          on membership.roleid = parent_role.oid
         and membership.member = member_role.oid
         and membership.set_option
         and not membership.inherit_option
        where membership.roleid is null
        order by required.parent_role, required.member_role
        """
    ).fetchall()
    return [
        f"GRANT {_cell(row, 0)} TO {_cell(row, 1)} WITH INHERIT FALSE, SET TRUE;"
        for row in rows
    ]


def _public_owner_fixes(conn) -> list[str]:
    rows = conn.execute(
        f"""
        with public_relations as (
          select case c.relkind
                   when 'S' then 'SEQUENCE'
                   when 'v' then 'VIEW'
                   when 'm' then 'MATERIALIZED VIEW'
                   when 'f' then 'FOREIGN TABLE'
                   else 'TABLE'
                 end as object_kind,
                 c.relname as object_name,
                 r.rolname as owner_name
          from pg_class c
          join pg_namespace n on n.oid = c.relnamespace
          join pg_roles r on r.oid = c.relowner
          where n.nspname = 'public'
            and c.relkind in ('r', 'p', 'f', 'S', 'v', 'm')
            and r.rolname <> '{MIGRATION_ROLE}'
            and not exists (
              select 1
              from pg_depend d
              where d.classid = 'pg_class'::regclass
                and d.objid = c.oid
                and d.deptype = 'e'
            )
        ),
        public_routines as (
          select 'ROUTINE' as object_kind,
                 p.oid::regprocedure::text as object_name,
                 r.rolname as owner_name
          from pg_proc p
          join pg_namespace n on n.oid = p.pronamespace
          join pg_roles r on r.oid = p.proowner
          where n.nspname = 'public'
            and r.rolname <> '{MIGRATION_ROLE}'
            and not exists (
              select 1
              from pg_depend d
              where d.classid = 'pg_proc'::regclass
                and d.objid = p.oid
                and d.deptype = 'e'
            )
        ),
        public_types as (
          select 'TYPE' as object_kind,
                 t.typname as object_name,
                 r.rolname as owner_name
          from pg_type t
          join pg_namespace n on n.oid = t.typnamespace
          join pg_roles r on r.oid = t.typowner
          where n.nspname = 'public'
            and t.typcategory <> 'A'
            and t.typrelid = 0
            and r.rolname <> '{MIGRATION_ROLE}'
            and not exists (
              select 1
              from pg_depend d
              where d.classid = 'pg_type'::regclass
                and d.objid = t.oid
                and d.deptype = 'e'
            )
        )
        select case object_kind
                 when 'ROUTINE' then format('ALTER ROUTINE %s OWNER TO {MIGRATION_ROLE};', object_name)
                 when 'TYPE' then format('ALTER TYPE public.%I OWNER TO {MIGRATION_ROLE};', object_name)
                 else format('ALTER %s public.%I OWNER TO {MIGRATION_ROLE};', object_kind, object_name)
               end as fix_sql
        from (
          select * from public_relations
          union all
          select * from public_routines
          union all
          select * from public_types
        ) objects
        order by fix_sql
        """
    ).fetchall()
    return [str(_cell(row, 0)) for row in rows]


def _cell(row, index: int):
    if isinstance(row, Mapping):
        return list(row.values())[index]
    return row[index]
