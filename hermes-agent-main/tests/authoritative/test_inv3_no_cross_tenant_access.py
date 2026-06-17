"""AUTHORITATIVE — INVARIANT 3: No cross-tenant access (read / write / spend).

GOAL_RULES.md §3 invariant 3:

    "No cross-tenant access (read / write / spend) — owner resolved server-side from
     slug; non-owned => 404 (not 403); RLS denies the same at the DB layer; no tool
     accepts a caller-supplied `user_id`/`owner_user_id`."

This is the money-safety suite's authoritative pin for the tenant-isolation invariant.
Assume every caller is EVIL: an operator (Takyon user) trying to reach another operator's
business, AND a product sub-user trying to reach another sub-user's rows — through any
channel (CEO tool, control HTTP API, or a stray DB query).

The invariant is enforced at FOUR independent layers; this suite pins all four so none
can be silently weakened:

  1. Owner is resolved SERVER-SIDE from the slug — never trusted from the caller.
     * core.TakyonStore._enforce_operator_business_access — operator CEO-tool plane.
     * policy.decide_execution / app_payments._resolve_owner — spend plane.
  2. A slug the caller does not own returns 404 (NOT 403) on the control HTTP API, so the
     surface is not a cross-tenant existence oracle.
     * control_api business endpoints: `slug not in principal.business_slugs` => 404.
  3. The DB enforces the same boundary independently via RLS (the runtime can't be the
     only gate).
     * db/migrations/0027_app_plane_rls.sql — enable + FORCE RLS, bypass default-off.
     * core.TakyonStore._pg_app_scope — flips `takyon.rls_bypass` to '0' for app routes.
  4. No business tool / no HTTP endpoint accepts a caller-supplied owner identity.
     * core.TAKYON_TOOL_DEFINITIONS — no schema declares `owner_user_id`/`user_id`.
     * control_plane.ResolvedPrincipal — `business_slugs` derived from owner_user_id,
       principal resolved from the bearer header only (control_api._resolve_principal).

Grounding (re-confirmed by reading the source before writing this file):
  plugins/takyon/core.py
    * class TakyonStore                              (~10943)
    * TakyonStore._active_operator_user_id           (~11072) — owner principal from session, not args
    * TakyonStore._enforce_operator_business_access  (~11075) — owner from slug; denies non-owner (~11113)
    * TakyonStore._pg_app_scope                      (~11190) — sets takyon.rls_bypass='0' + binds slug/user/session
    * _schema()                                      (~17210) — tool schema builder
    * TAKYON_TOOL_DEFINITIONS                        (~28381) — canonical list of business tool defs
  plugins/takyon/policy.py
    * decide_execution(conn, *, business_slug, ...)  (~193) — resolves owner from slug (~233), no owner kwarg
  plugins/takyon/app_payments.py
    * _resolve_owner(conn, business_slug)            (~769) — owner from slug; raises if missing
  plugins/takyon/control_api.py
    * _resolve_principal(authorization, conn)        (~778) — principal from bearer header only
    * get_business / creative-credit endpoints       (~1119+) — `slug not in principal.business_slugs` => 404
  plugins/takyon/control_plane.py
    * ResolvedPrincipal                              (~32) — user_id/key_id/status/business_slugs
    * _business_slugs_for_user(conn, user_id)        (~43) — slugs FROM businesses WHERE owner_user_id = %s
    * resolve_api_key(conn, raw_key)                 (~304) — server-side hash resolve, no caller user_id
  plugins/takyon/db/migrations/0027_app_plane_rls.sql — app-plane RLS policies

Most assertions need NO credentials/network — they import the real symbol and assert its
signature + source structure. The behavioral end-to-end checks use the repo `pg_conn`
fixture and skip automatically when psycopg / TAKYON_TEST_PG_DSN are absent.
"""

from __future__ import annotations

import inspect
import re
import uuid
from pathlib import Path

import pytest

from plugins.takyon import app_payments, core, policy
from plugins.takyon.control_plane import ResolvedPrincipal

# --------------------------------------------------------------------------------------
# Shared source handles (read once; assert structure without executing privileged paths).
# --------------------------------------------------------------------------------------

_CORE_SRC = inspect.getsource(core)
_CONTROL_API_PATH = Path(core.__file__).with_name("control_api.py")
_CONTROL_API_SRC = _CONTROL_API_PATH.read_text(encoding="utf-8")
_RLS_MIGRATION_PATH = (
    Path(core.__file__).with_name("db") / "migrations" / "0027_app_plane_rls.sql"
)

# Identity kwargs that, if accepted from the caller, would let an EVIL user assert a
# different owner than the one the slug resolves to. None of these may appear as a
# *caller-supplied* parameter on a tenant-resolving tool/endpoint/spend path.
_CALLER_SUPPLIED_OWNER_TOKENS = frozenset({"owner_user_id", "user_id", "owner_id"})


# ── 1. Owner resolved SERVER-SIDE from the slug (operator CEO-tool plane) ─────────────


def test_enforce_operator_business_access_takes_only_conn_and_slug():
    """The operator access gate resolves the owner from the slug alone. It must NOT accept
    a caller-supplied owner/user id — otherwise an EVIL operator could assert ownership of
    a business they do not own."""
    fn = core.TakyonStore._enforce_operator_business_access
    params = list(inspect.signature(fn).parameters)
    # self, conn, business_slug — and nothing that lets the caller name the owner.
    assert params == ["self", "conn", "business_slug"], params
    assert not (_CALLER_SUPPLIED_OWNER_TOKENS & set(params)), params


def test_enforce_operator_business_access_resolves_owner_from_db_and_denies_mismatch():
    """Source-structure pin: the gate reads owner_user_id FROM businesses WHERE slug = ?
    (server-side, single source of truth), compares it against the session-bound operator,
    and raises 'access denied' on mismatch."""
    src = inspect.getsource(core.TakyonStore._enforce_operator_business_access)
    # Owner is read from the DB keyed on the slug — not taken from the caller.
    assert re.search(
        r"SELECT\s+owner_user_id\s+FROM\s+businesses\s+WHERE\s+slug\s*=\s*\?",
        src,
        re.IGNORECASE,
    ), "owner must be resolved server-side from the slug"
    # The bound principal comes from the session, not from a tool argument.
    assert "self._active_operator_user_id()" in src
    # Mismatch => deny. (A non-owner is refused; existence is checked first separately.)
    assert "if owner_user_id != operator_user_id:" in src
    assert 'raise TakyonError(f"access denied for business:{business_slug}")' in src


def test_active_operator_user_id_comes_from_session_not_tool_arguments():
    """The bound operator principal is derived from the session/process identity, never
    from a tool/request payload, so a caller cannot inject a different principal.

    `_active_operator_user_id()` takes only `self` and returns the stored instance field
    `_operator_user_id`. That field is bound ONCE in `TakyonStore.__init__` from the
    session env (`TAKYON_SESSION_USER_ID` via get_session_env) or an explicit constructor
    arg — never re-derived from a tool/request payload."""
    getter_src = inspect.getsource(core.TakyonStore._active_operator_user_id)
    params = list(inspect.signature(core.TakyonStore._active_operator_user_id).parameters)
    assert params == ["self"], params
    # The getter only returns stored instance state; it does not read any caller input.
    assert "return self._operator_user_id" in getter_src

    # The stored field is bound from the session/constructor in __init__, not from a tool arg.
    init_src = inspect.getsource(core.TakyonStore.__init__)
    assert 'get_session_env("TAKYON_SESSION_USER_ID"' in init_src
    assert re.search(
        r"self\._operator_user_id\s*=\s*str\(\s*operator_user_id\s*\n\s*or session_user_id",
        init_src,
    ), "operator principal must come from the constructor arg / session, not a tool payload"


# ── 1b. Owner resolved SERVER-SIDE from the slug (spend plane) ────────────────────────


def test_decide_execution_resolves_owner_from_slug_with_no_caller_owner_kwarg():
    """The spend-routing decision resolves the business owner from the slug; the caller
    cannot pass a mismatched user. A caller-supplied owner kwarg would be a spend-side
    cross-tenant hole."""
    params = list(inspect.signature(policy.decide_execution).parameters)
    assert "business_slug" in params
    assert not (_CALLER_SUPPLIED_OWNER_TOKENS & set(params)), params
    src = inspect.getsource(policy.decide_execution)
    assert re.search(
        r"select\s+owner_user_id\s+from\s+businesses\s+where\s+slug\s*=\s*%s",
        src,
        re.IGNORECASE,
    ), "decide_execution must resolve owner server-side from slug"


def test_resolve_owner_reads_owner_from_slug_only():
    """app_payments._resolve_owner derives the payout owner from the slug alone."""
    params = list(inspect.signature(app_payments._resolve_owner).parameters)
    assert params == ["conn", "business_slug"], params
    src = inspect.getsource(app_payments._resolve_owner)
    assert re.search(
        r"select\s+owner_user_id\s+from\s+businesses\s+where\s+slug\s*=\s*%s",
        src,
        re.IGNORECASE,
    )


# ── 2. Non-owned => 404 (NOT 403) on the control HTTP API ─────────────────────────────


def test_control_api_business_guard_returns_404_never_403():
    """Every authenticated business endpoint consults ONLY the caller's owned set
    (`slug not in principal.business_slugs`) and returns 404 — never 403, never the row.

    404 (not 403) is load-bearing: a 403 would confirm "exists but not yours" and turn the
    surface into a cross-tenant existence oracle. We assert (a) there is at least one such
    guard, (b) every guard is paired with a 404, and (c) no 403 leaks from this surface."""
    guard_lines = re.findall(
        r"slug not in principal\.business_slugs", _CONTROL_API_SRC
    )
    assert guard_lines, "expected at least one ownership guard on the control API"

    # Each guard must be immediately followed by a 404 (not_found) refusal.
    for m in re.finditer(
        r"if\s+slug not in principal\.business_slugs:\s*\n\s*raise HTTPException\(\s*status_code=(\d+)\s*,\s*detail=\"([^\"]+)\"",
        _CONTROL_API_SRC,
    ):
        assert m.group(1) == "404", f"ownership guard must 404, got {m.group(1)}"
        assert m.group(2) == "not_found", m.group(2)

    # Belt-and-suspenders: this surface must never answer a cross-tenant request with 403.
    assert "status_code=403" not in _CONTROL_API_SRC, (
        "control API must not 403 — a non-owned slug is indistinguishable from "
        "nonexistent (404), or the surface becomes an existence oracle"
    )


def test_every_business_slug_guard_is_followed_by_404():
    """Count discipline: # of ownership guards == # that 404. No guard may fall through to
    a different status (or to no refusal at all)."""
    guards = len(re.findall(r"slug not in principal\.business_slugs", _CONTROL_API_SRC))
    paired_404 = len(
        re.findall(
            r"slug not in principal\.business_slugs:\s*\n\s*raise HTTPException\(\s*status_code=404",
            _CONTROL_API_SRC,
        )
    )
    assert guards == paired_404, (guards, paired_404)


# ── 3. The DB enforces the same boundary independently via RLS ────────────────────────


def test_rls_migration_exists():
    assert _RLS_MIGRATION_PATH.exists(), _RLS_MIGRATION_PATH


def test_rls_enables_and_forces_row_level_security_on_app_tables():
    """RLS must be both ENABLED and FORCED on the shared app-plane tables, so even the
    table owner role is subject to the policies (no silent bypass via ownership)."""
    sql = _RLS_MIGRATION_PATH.read_text(encoding="utf-8").lower()
    # The customer-data tables that must be tenant-isolated at the DB layer.
    for table in ("app_records", "app_user_profiles", "app_connections", "app_entitlements"):
        assert f"alter table if exists {table} enable row level security" in sql, table
        assert f"alter table if exists {table} force row level security" in sql, table


def test_rls_policies_scope_every_op_by_business_slug_and_default_bypass_off():
    """Each policy gates on the request-bound business slug, and bypass DEFAULTS TO OFF
    (only an explicit '1'/'true'/'on' enables it). An app route that forgets to set bypass
    is therefore denied, not allowed (fail-closed)."""
    sql = _RLS_MIGRATION_PATH.read_text(encoding="utf-8").lower()
    # Bypass helper defaults the unset setting to '0' (off) — fail-closed.
    assert "coalesce(nullif(current_setting('takyon.rls_bypass', true), ''), '0')" in sql
    assert "in ('1', 'true', 'on')" in sql
    # Tenant scoping is keyed on the request-bound slug, present on the policies.
    assert "business_slug = takyon_rls_business_slug()" in sql
    # All four CRUD verbs are covered on the core customer-record table.
    for verb in ("for select", "for insert", "for update", "for delete"):
        assert verb in sql, verb


def test_pg_app_scope_flips_bypass_off_and_binds_request_scope():
    """The app-facing runtime scope explicitly turns RLS bypass OFF for the request and
    binds the live business plus the app-user / session hash, so the DB enforces the same
    customer boundary as the runtime. Internal operator code keeps bypass (full authority);
    app routes deliberately drop it."""
    src = inspect.getsource(core.TakyonStore._pg_app_scope)
    # Bypass is set to '0' for the duration of the app request.
    assert "set_config('takyon.rls_bypass', '0', true)" in src
    # The slug is bound from the argument (server-chosen), and the customer identity is the
    # app_user_id / hashed session token — not a free-form owner id.
    assert "takyon.rls_business_slug" in src
    assert "takyon.rls_app_user_id" in src
    assert "takyon.rls_session_hash" in src
    # Session tokens are bound as a HASH, never raw.
    assert "_hash_token(" in src
    params = list(inspect.signature(core.TakyonStore._pg_app_scope).parameters)
    # self, conn, business_slug, app_user_id, session_token — no owner/user override.
    assert not (_CALLER_SUPPLIED_OWNER_TOKENS & set(params)), params


# ── 4. No tool / endpoint accepts a caller-supplied owner identity ────────────────────


def test_no_business_tool_schema_accepts_caller_supplied_owner_identity():
    """The authoritative pin: iterate EVERY business tool definition and assert none of
    them declare `owner_user_id`/`user_id`/`owner_id` as an input property. The tenant key
    a tool accepts is the business `slug` (resolved server-side to an owner); the customer
    key is `app_user_id`/`session_token` (bound to the scoped session at the DB). A tool
    that let the caller pass an owner id would be a cross-tenant write/spend hole."""
    defs = core.TAKYON_TOOL_DEFINITIONS
    assert isinstance(defs, list) and defs, "expected a non-empty tool-definition list"

    offenders: list[tuple[str, str]] = []
    saw_business_key = False
    for d in defs:
        name = str(d.get("name") or "")
        schema = d.get("schema") or {}
        props = (schema.get("parameters") or {}).get("properties") or {}
        if "business" in props:
            saw_business_key = True
        for prop_name in props:
            if prop_name in _CALLER_SUPPLIED_OWNER_TOKENS:
                offenders.append((name, prop_name))

    assert not offenders, f"tools expose caller-supplied owner identity: {offenders}"
    # Sanity: the suite actually inspected business-scoped tools (slug is the tenant key).
    assert saw_business_key, "expected business-scoped tools keyed on `business` slug"


def test_resolved_principal_is_minimal_and_slugs_are_server_derived():
    """ResolvedPrincipal carries only identity + the OWNED slug set. The slug set is derived
    server-side from `owner_user_id` — the caller never supplies it. The dataclass exposes
    no secret/internal handle a caller could use to escape its tenant."""
    fields = list(ResolvedPrincipal.__dataclass_fields__)
    assert fields == ["user_id", "key_id", "status", "business_slugs"], fields
    # The owned-slug derivation is keyed on owner_user_id, server-side.
    cp_src = Path(core.__file__).with_name("control_plane.py").read_text(encoding="utf-8")
    assert re.search(
        r"def _business_slugs_for_user\(conn, user_id: str\)", cp_src
    ), "slug set must be derived from the resolved user_id"
    assert re.search(
        r"select slug from businesses where owner_user_id = %s", cp_src, re.IGNORECASE
    ), "owned slugs come from businesses.owner_user_id, not from the caller"


def test_control_api_principal_resolved_from_bearer_header_only():
    """The principal is resolved from the Authorization bearer header alone (then a
    server-side hashed-key lookup). No business endpoint takes a caller-supplied user_id /
    owner_user_id as a path/query/body parameter — the only owner identity is the one the
    server resolves from the key."""
    # _resolve_principal reads only the Authorization header + the control conn.
    m = re.search(
        r"def _resolve_principal\(\s*authorization: str \| None = Header\(default=None\)\s*,\s*conn=Depends\(get_control_conn\)\s*,?\s*\)",
        _CONTROL_API_SRC,
    )
    assert m, "principal must be resolved from the bearer header only"
    # Authenticated endpoints inject the principal via Depends — never accept it as input.
    assert "Depends(_rate_limited_principal)" in _CONTROL_API_SRC
    # No FastAPI route parameter named owner_user_id (would let a caller name the owner).
    assert not re.search(
        r"\n\s*owner_user_id\s*:\s*str\s*=", _CONTROL_API_SRC
    ), "no endpoint may accept a caller-supplied owner_user_id"


# ══════════════════════════════════════════════════════════════════════════════════════
# 5. PG behavioral end-to-end: RLS actually denies cross-tenant reads/writes at the DB.
#
# Exercises the real 0027 RLS policies on a real Postgres rig via the repo `pg_conn`
# fixture. Skips automatically when psycopg is missing or TAKYON_TEST_PG_DSN is unset
# (the fixture calls pytest.skip). This is the independent DB-layer proof that the runtime
# is not the only gate.
# ══════════════════════════════════════════════════════════════════════════════════════

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_identity  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402


def _owner(conn) -> str:
    uid, _, _ = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}")
    return uid


def _business(conn, owner_id) -> str:
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", owner_id),
    )
    return slug


def _app_user(conn, slug, email) -> str:
    return str(app_identity.upsert_app_user(conn, slug, email).id)


def _insert_record_bypass(conn, slug, app_user_id, record_id) -> None:
    """Insert one app_records row with RLS bypass ON (operator/service authority)."""
    with conn.transaction():
        conn.execute("select set_config('takyon.rls_bypass', '1', true)")
        conn.execute(
            "insert into app_records (id, business_slug, app_user_id, record_type, data) "
            "values (%s, %s, %s, %s, %s)",
            (record_id, slug, app_user_id, "note", "{}"),
        )


def _scope_to_app_user(conn, slug, app_user_id) -> None:
    """Bind the connection to one customer scope with RLS bypass OFF — mirrors
    core._pg_app_scope's request-local binding."""
    conn.execute("select set_config('takyon.rls_bypass', '0', true)")
    conn.execute("select set_config('takyon.rls_business_slug', %s, true)", (slug,))
    conn.execute("select set_config('takyon.rls_app_user_id', %s, true)", (app_user_id,))
    conn.execute("select set_config('takyon.rls_session_hash', '', true)")


@pytest.mark.pg
def test_pg_rls_denies_cross_tenant_record_read(pg_conn):
    """A sub-user scoped to (business A, user A) cannot READ another business's record.
    RLS returns zero rows — the DB itself, not just the runtime, enforces the boundary."""
    owner_a = _owner(pg_conn)
    owner_b = _owner(pg_conn)
    slug_a = _business(pg_conn, owner_a)
    slug_b = _business(pg_conn, owner_b)
    user_a = _app_user(pg_conn, slug_a, "a@example.com")
    user_b = _app_user(pg_conn, slug_b, "b@example.com")

    rec_a = f"rec-{uuid.uuid4().hex[:8]}"
    rec_b = f"rec-{uuid.uuid4().hex[:8]}"
    _insert_record_bypass(pg_conn, slug_a, user_a, rec_a)
    _insert_record_bypass(pg_conn, slug_b, user_b, rec_b)

    # Scope to tenant A's customer. Under RLS they can see their own row...
    _scope_to_app_user(pg_conn, slug_a, user_a)
    own = pg_conn.execute(
        "select id from app_records where id = %s", (rec_a,)
    ).fetchone()
    assert own is not None and str(own[0]) == rec_a

    # ...but the other tenant's row is invisible (cross-tenant read denied).
    cross = pg_conn.execute(
        "select id from app_records where id = %s", (rec_b,)
    ).fetchone()
    assert cross is None, "RLS must hide another tenant's record"

    # And a blanket select returns ONLY their own row.
    visible = {
        str(r[0]) for r in pg_conn.execute("select id from app_records").fetchall()
    }
    assert visible == {rec_a}, visible


@pytest.mark.pg
def test_pg_rls_denies_cross_tenant_record_write(pg_conn):
    """A sub-user scoped to (business A, user A) cannot WRITE a row that claims another
    tenant. The insert is rejected by the policy's WITH CHECK (RLS write deny)."""
    owner_a = _owner(pg_conn)
    owner_b = _owner(pg_conn)
    slug_a = _business(pg_conn, owner_a)
    slug_b = _business(pg_conn, owner_b)
    user_a = _app_user(pg_conn, slug_a, "a@example.com")
    user_b = _app_user(pg_conn, slug_b, "b@example.com")

    _scope_to_app_user(pg_conn, slug_a, user_a)

    # Attempt to write into tenant B (cross-tenant write) — must be refused by RLS.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg_conn.execute(
            "insert into app_records (id, business_slug, app_user_id, record_type, data) "
            "values (%s, %s, %s, %s, %s)",
            (f"rec-{uuid.uuid4().hex[:8]}", slug_b, user_b, "note", "{}"),
        )


@pytest.mark.pg
def test_pg_rls_denies_writing_a_record_for_a_different_user_in_same_business(pg_conn):
    """No cross-USER writes even within the same business: scoped as user A, you cannot
    insert a record attributed to user B. (Closes the 'same tenant, other customer' hole.)"""
    owner = _owner(pg_conn)
    slug = _business(pg_conn, owner)
    user_a = _app_user(pg_conn, slug, "a@example.com")
    user_b = _app_user(pg_conn, slug, "b@example.com")

    _scope_to_app_user(pg_conn, slug, user_a)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg_conn.execute(
            "insert into app_records (id, business_slug, app_user_id, record_type, data) "
            "values (%s, %s, %s, %s, %s)",
            (f"rec-{uuid.uuid4().hex[:8]}", slug, user_b, "note", "{}"),
        )
