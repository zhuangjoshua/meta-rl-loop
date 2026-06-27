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

from plugins.takyon import ai_gateway, app_actions, app_connections, app_directory, app_email, app_identity, app_media, app_payments, app_usage, core, policy, safebox_app
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
_RLS_BYPASS_HARDENING_PATH = (
    Path(core.__file__).with_name("db") / "migrations" / "0043_harden_rls_bypass_role_gate.sql"
)
_AUTHORITY_SPLIT_ROLES_PATH = (
    Path(core.__file__).with_name("db") / "migrations" / "0044_authority_split_login_roles.sql"
)
_APP_RUNTIME_IDENTITY_PORTS_PATH = (
    Path(core.__file__).with_name("db") / "migrations" / "0045_app_runtime_identity_ports.sql"
)
_REVOKE_LEGACY_CROSS_PLANE_MEMBERSHIPS_PATH = (
    Path(core.__file__).with_name("db") / "migrations" / "0046_revoke_legacy_cross_plane_role_memberships.sql"
)
_APP_RUNTIME_MONEY_READ_PORTS_PATH = (
    Path(core.__file__).with_name("db") / "migrations" / "0047_app_runtime_money_read_ports.sql"
)
_APP_RUNTIME_SESSION_USAGE_PORTS_PATH = (
    Path(core.__file__).with_name("db") / "migrations" / "0048_app_runtime_session_usage_ports.sql"
)
_REVOKE_APP_CHECKOUT_SESSIONS_PATH = (
    Path(core.__file__).with_name("db") / "migrations" / "0049_revoke_app_checkout_session_direct_access.sql"
)
_IGNORE_APP_USER_ID_GUC_FOR_APP_ROLES_PATH = (
    Path(core.__file__).with_name("db") / "migrations" / "0050_ignore_app_user_id_guc_for_app_roles.sql"
)
_SESSION_BOUND_APP_MEDIA_USAGE_PATH = (
    Path(core.__file__).with_name("db") / "migrations" / "0051_session_bound_app_media_usage.sql"
)
_AUTHORITY_SPLIT_CONTROL_PLANE_RLS_PATH = (
    Path(core.__file__).with_name("db") / "migrations" / "0052_authority_split_control_plane_rls_policies.sql"
)
_APP_SUPABASE_SESSION_UUID_CAST_PATH = (
    Path(core.__file__).with_name("db") / "migrations" / "0053_fix_app_supabase_session_uuid_cast.sql"
)

# Identity kwargs that, if accepted from the caller, would let an EVIL user assert a
# different owner than the one the slug resolves to. None of these may appear as a
# *caller-supplied* parameter on a tenant-resolving tool/endpoint/spend path.
_CALLER_SUPPLIED_OWNER_TOKENS = frozenset({"owner_user_id", "user_id", "owner_id"})


class _FakeSQLResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeRoleCursor:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=None):
        return self.raw.execute(sql, params)

    def close(self):
        self.raw.closed_cursors += 1


class _FakeRoleRaw:
    def __init__(self, *, session_user: str, current_user: str):
        self.session_user = session_user
        self.current_user = current_user
        self.statements: list[tuple[str, object]] = []
        self.closed_cursors = 0

    def execute(self, sql, params=None):
        self.statements.append((str(sql), params))
        normalized = str(sql).lower()
        if "session_user::text" in normalized and "current_user::text" in normalized:
            return _FakeSQLResult({"session_user": self.session_user, "current_user": self.current_user})
        if "current_setting" in normalized:
            return _FakeSQLResult(("",))
        return _FakeSQLResult((None,))

    def cursor(self):
        return _FakeRoleCursor(self)


class _FakePGConn:
    def __init__(self, *, session_user: str, current_user: str):
        self._pg = _FakeRoleRaw(session_user=session_user, current_user=current_user)


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


def test_rls_bypass_requires_allowed_current_user_not_guc_alone():
    """An app role setting takyon.rls_bypass=1 must not become operator authority."""
    assert _RLS_BYPASS_HARDENING_PATH.exists(), _RLS_BYPASS_HARDENING_PATH
    sql = _RLS_BYPASS_HARDENING_PATH.read_text(encoding="utf-8").lower()

    assert "create or replace function takyon_rls_bypass()" in sql
    assert "current_user in" in sql
    for allowed in (
        "takyon_runtime",
        "takyon_operator_runtime",
        "takyon_safebox_authority",
        "takyon_migration",
    ):
        assert f"'{allowed}'" in sql
    assert "'takyon_app'" not in sql.split("current_user in", 1)[1].split(")", 1)[0]
    assert "'takyon_app_runtime'" not in sql.split("current_user in", 1)[1].split(")", 1)[0]
    assert "current_setting('takyon.rls_bypass', true)" in sql
    assert " and coalesce(nullif(current_setting('takyon.rls_bypass', true), ''), '0')" in sql


def test_app_roles_cannot_self_select_app_user_by_guc():
    """App roles may bind a session hash, but not claim another customer by setting app_user_id."""
    assert _IGNORE_APP_USER_ID_GUC_FOR_APP_ROLES_PATH.exists(), _IGNORE_APP_USER_ID_GUC_FOR_APP_ROLES_PATH
    sql = _IGNORE_APP_USER_ID_GUC_FOR_APP_ROLES_PATH.read_text(encoding="utf-8").lower()

    assert "create or replace function takyon_rls_bound_app_user_id()" in sql
    assert "current_user in ('takyon_app', 'takyon_app_runtime')" in sql
    assert "then null::uuid" in sql
    assert "else nullif(current_setting('takyon.rls_app_user_id', true), '')::uuid" in sql


def test_authority_split_roles_do_not_create_cross_plane_memberships():
    """The target login roles must be separate authorities, not SET ROLE wrappers around each other."""
    assert _AUTHORITY_SPLIT_ROLES_PATH.exists(), _AUTHORITY_SPLIT_ROLES_PATH
    assert _REVOKE_LEGACY_CROSS_PLANE_MEMBERSHIPS_PATH.exists(), _REVOKE_LEGACY_CROSS_PLANE_MEMBERSHIPS_PATH
    sql = _AUTHORITY_SPLIT_ROLES_PATH.read_text(encoding="utf-8").lower()
    revoke_sql = _REVOKE_LEGACY_CROSS_PLANE_MEMBERSHIPS_PATH.read_text(encoding="utf-8").lower()

    for role in (
        "takyon_operator_runtime",
        "takyon_app_runtime",
        "takyon_safebox_authority",
        "takyon_migration",
    ):
        assert f"create role {role} login" in sql

    forbidden_memberships = (
        "grant takyon_app to takyon_operator_runtime",
        "grant takyon_operator_runtime to takyon_app_runtime",
        "grant takyon_safebox_authority to takyon_app_runtime",
        "grant takyon_operator_runtime to takyon_safebox_authority",
        "grant takyon_app_runtime to takyon_operator_runtime",
    )
    for grant in forbidden_memberships:
        assert grant not in sql

    assert "revoke %i from %i" in revoke_sql
    for parent, member in (
        ("takyon_app", "takyon_runtime"),
        ("takyon_app", "takyon_operator_runtime"),
        ("takyon_app_runtime", "takyon_operator_runtime"),
        ("takyon_operator_runtime", "takyon_app_runtime"),
        ("takyon_safebox_authority", "takyon_operator_runtime"),
    ):
        assert f"('{parent}', '{member}')" in revoke_sql

    assert "revoke insert, update, delete on\n    app_usage_events,\n    app_entitlements,\n    app_revenue_events\n    from takyon_app_runtime" in sql
    assert "revoke insert, update, delete on\n    billing_accounts" in sql


def test_split_authority_roles_have_control_plane_rls_policies_but_app_roles_do_not():
    assert _AUTHORITY_SPLIT_CONTROL_PLANE_RLS_PATH.exists(), _AUTHORITY_SPLIT_CONTROL_PLANE_RLS_PATH
    sql = _AUTHORITY_SPLIT_CONTROL_PLANE_RLS_PATH.read_text(encoding="utf-8").lower()

    for role in (
        "takyon_operator_runtime",
        "takyon_safebox_authority",
        "takyon_migration",
    ):
        assert f"'{role}'" in sql
        assert "for all to %i" in sql
        assert "using (takyon_rls_bypass())" in sql
        assert "with check (takyon_rls_bypass())" in sql

    authority_block = sql.split("authority_roles text[] := array[", 1)[1].split("];", 1)[0]
    assert "takyon_app_runtime" not in authority_block
    assert "'takyon_app'" not in authority_block
    assert "'businesses'" in sql
    assert "'business_work_requests'" in sql
    assert "'jobs'" in sql


def test_pg_app_scope_flips_bypass_off_and_binds_request_scope():
    """The app-facing runtime scope explicitly turns RLS bypass OFF for the request and binds the live
    business plus the app-user / session hash, so the DB enforces the same customer boundary as the
    runtime. It must not demote an operator-capable session with SET ROLE; app traffic starts on the
    app DB login or fails closed."""
    src = inspect.getsource(core.TakyonStore._pg_app_scope)
    # Bypass is set to '0' for the duration of the app request.
    assert "set_config('takyon.rls_bypass', '0', true)" in src
    # Direct app-plane login roles use the app scope without creating an operator->app SET ROLE bridge;
    # leaked or non-app role state is refused before binding request GUCs.
    assert 'assert_takyon_pg_role(raw, "app")' in src
    assert "app scope requires an app-plane database login" in src
    assert "set local role" not in src.lower()
    assert "set role" not in src.lower()
    assert "reset role" not in src.lower()
    assert "used_set_role" not in src
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

    usage_src = inspect.getsource(app_usage._ledger_gate_scope)
    assert "plane: str" in usage_src
    assert "assert_takyon_pg_role(raw, expected_plane)" in usage_src
    assert "usage ledger gate requires a" in usage_src
    assert "set role" not in usage_src.lower()
    assert "reset role" not in usage_src.lower()
    assert "used_set_role" not in usage_src


def test_store_app_plane_connections_do_not_request_rls_bypass():
    src = inspect.getsource(core.TakyonStore._connect_postgres)

    assert 'configure_takyon_pg_session(conn, bypass=self._database_plane != "app")' in src
    assert "configure_takyon_pg_session(conn, bypass=True)" not in src


def test_safebox_db_conn_initializes_authority_rls_session():
    src = inspect.getsource(safebox_app._safebox_db_conn)

    assert "resolve_database_url(plane=\"safebox\")" in src
    assert "configure_takyon_pg_session(raw_conn, bypass=True)" in src
    assert 'assert_takyon_pg_role(raw_conn, "safebox")' in src


def test_app_checkout_commits_intent_before_safebox_stripe_call():
    src = inspect.getsource(core.handle_business_create_app_checkout)

    intent_pos = src.index("INSERT INTO app_checkout_intents")
    commit_pos = src.index("conn.commit()", intent_pos)
    stripe_pos = src.index('safebox.stripe_request("checkout/sessions"', intent_pos)
    assert intent_pos < commit_pos < stripe_pos


def test_pg_app_scope_rejects_operator_session_without_role_change(monkeypatch):
    monkeypatch.setattr(core, "_PGConn", _FakePGConn)
    store = core.TakyonStore.__new__(core.TakyonStore)
    conn = _FakePGConn(
        session_user="takyon_operator_runtime",
        current_user="takyon_operator_runtime",
    )

    with pytest.raises(core.TakyonError, match="app-plane database login"):
        with store._pg_app_scope(conn, "acme", app_user_id="u_1"):
            raise AssertionError("scope should fail before yielding")

    sql = "\n".join(statement.lower() for statement, _ in conn._pg.statements)
    assert "set role" not in sql
    assert "set local role" not in sql
    assert "reset role" not in sql
    assert "takyon.rls_business_slug" not in sql


def test_pg_app_scope_binds_direct_app_session_without_role_change(monkeypatch):
    monkeypatch.setattr(core, "_PGConn", _FakePGConn)
    store = core.TakyonStore.__new__(core.TakyonStore)
    conn = _FakePGConn(
        session_user="takyon_app_runtime",
        current_user="takyon_app_runtime",
    )

    with store._pg_app_scope(conn, "acme", session_token="session-secret"):
        pass

    sql = "\n".join(statement.lower() for statement, _ in conn._pg.statements)
    assert "set role" not in sql
    assert "set local role" not in sql
    assert "reset role" not in sql
    assert "takyon.rls_bypass" in sql
    assert "takyon.rls_business_slug" in sql
    assert "takyon.rls_session_hash" in sql


def test_pg_app_scope_rejects_legacy_app_role_by_default(monkeypatch):
    monkeypatch.delenv("TAKYON_ALLOW_LEGACY_DB_ROLES", raising=False)
    monkeypatch.setattr(core, "_PGConn", _FakePGConn)
    store = core.TakyonStore.__new__(core.TakyonStore)
    conn = _FakePGConn(
        session_user="takyon_app",
        current_user="takyon_app",
    )

    with pytest.raises(core.TakyonError, match="app-plane database login"):
        with store._pg_app_scope(conn, "acme", session_token="session-secret"):
            raise AssertionError("scope should fail before yielding")

    sql = "\n".join(statement.lower() for statement, _ in conn._pg.statements)
    assert "set role" not in sql
    assert "reset role" not in sql
    assert "takyon.rls_business_slug" not in sql


def test_pg_app_scope_accepts_legacy_app_role_only_with_cutover_opt_in(monkeypatch):
    monkeypatch.setenv("TAKYON_ALLOW_LEGACY_DB_ROLES", "1")
    monkeypatch.setattr(core, "_PGConn", _FakePGConn)
    store = core.TakyonStore.__new__(core.TakyonStore)
    conn = _FakePGConn(
        session_user="takyon_app",
        current_user="takyon_app",
    )

    with store._pg_app_scope(conn, "acme", session_token="session-secret"):
        pass

    sql = "\n".join(statement.lower() for statement, _ in conn._pg.statements)
    assert "set role" not in sql
    assert "reset role" not in sql
    assert "takyon.rls_business_slug" in sql


def test_usage_ledger_gate_rejects_operator_session_without_role_change():
    raw = _FakeRoleRaw(
        session_user="takyon_operator_runtime",
        current_user="takyon_operator_runtime",
    )

    with pytest.raises(RuntimeError, match="app database login"):
        with app_usage._ledger_gate_scope(raw, plane="app"):
            raise AssertionError("scope should fail before yielding")

    sql = "\n".join(statement.lower() for statement, _ in raw.statements)
    assert "set role" not in sql
    assert "reset role" not in sql
    assert "takyon.rls_bypass" not in sql


def test_usage_ledger_gate_binds_direct_app_session_without_role_change():
    raw = _FakeRoleRaw(
        session_user="takyon_app_runtime",
        current_user="takyon_app_runtime",
    )

    with app_usage._ledger_gate_scope(raw, plane="app"):
        pass

    sql = "\n".join(statement.lower() for statement, _ in raw.statements)
    assert "set role" not in sql
    assert "reset role" not in sql
    assert "takyon.rls_bypass" in sql
    assert raw.closed_cursors == 1


def test_usage_ledger_gate_rejects_legacy_app_role_by_default(monkeypatch):
    monkeypatch.delenv("TAKYON_ALLOW_LEGACY_DB_ROLES", raising=False)
    raw = _FakeRoleRaw(
        session_user="takyon_app",
        current_user="takyon_app",
    )

    with pytest.raises(RuntimeError, match="app database login"):
        with app_usage._ledger_gate_scope(raw, plane="app"):
            raise AssertionError("scope should fail before yielding")

    sql = "\n".join(statement.lower() for statement, _ in raw.statements)
    assert "set_config('takyon.rls_bypass'" not in sql


def test_usage_ledger_gate_accepts_legacy_app_role_only_with_cutover_opt_in(monkeypatch):
    monkeypatch.setenv("TAKYON_ALLOW_LEGACY_DB_ROLES", "1")
    raw = _FakeRoleRaw(
        session_user="takyon_app",
        current_user="takyon_app",
    )

    with app_usage._ledger_gate_scope(raw, plane="app"):
        pass

    sql = "\n".join(statement.lower() for statement, _ in raw.statements)
    assert "set role" not in sql
    assert "reset role" not in sql
    assert "takyon.rls_bypass" in sql


def test_usage_ledger_gate_binds_direct_safebox_session_without_role_change():
    raw = _FakeRoleRaw(
        session_user="takyon_safebox_authority",
        current_user="takyon_safebox_authority",
    )

    with app_usage._ledger_gate_scope(raw, plane="safebox"):
        pass

    sql = "\n".join(statement.lower() for statement, _ in raw.statements)
    assert "set role" not in sql
    assert "reset role" not in sql
    assert "takyon.rls_bypass" in sql
    assert raw.closed_cursors == 1


def test_subuser_host_plain_store_refuses_default_operator_plane(monkeypatch):
    """A subuser/product process must not fall back to the operator store when a route forgets
    to bind the app plane. The app route context is the only allowed way to construct the
    shared store on that host."""
    monkeypatch.setenv("TAKYON_HOST_ROLE", "subuser")

    with pytest.raises(core.TakyonError, match="subuser host cannot open the default operator store"):
        core._store()

    with core.app_runtime_database_plane():
        store = core._store()

    assert isinstance(store, core.TakyonStore)
    assert store._database_plane == "app"


def test_app_runtime_identity_session_ports_are_bounded():
    sql = _APP_RUNTIME_IDENTITY_PORTS_PATH.read_text(encoding="utf-8").lower()
    uuid_cast_sql = _APP_SUPABASE_SESSION_UUID_CAST_PATH.read_text(encoding="utf-8").lower()
    money_sql = _APP_RUNTIME_MONEY_READ_PORTS_PATH.read_text(encoding="utf-8").lower()
    usage_sql = _APP_RUNTIME_SESSION_USAGE_PORTS_PATH.read_text(encoding="utf-8").lower()
    checkout_sql = _REVOKE_APP_CHECKOUT_SESSIONS_PATH.read_text(encoding="utf-8").lower()
    media_usage_sql = _SESSION_BOUND_APP_MEDIA_USAGE_PATH.read_text(encoding="utf-8").lower()
    identity_src = inspect.getsource(app_identity.start_supabase_session)
    revoke_src = inspect.getsource(app_identity.revoke_session)
    supabase_login_src = inspect.getsource(core.handle_business_supabase_login)
    profile_src = inspect.getsource(core.handle_business_read_app_profile)

    assert "takyon_app_bind_supabase_session" in sql
    assert "takyon_app_validate_session" in sql
    assert "takyon_app_revoke_session" in sql
    assert "takyon_app_runtime_business" in sql
    assert "takyon_app_control_blocker" in sql
    assert "takyon_app_record_event" in sql
    assert "takyon_app_media_usage" in sql
    assert "takyon_app_service_email_recipient" in sql
    assert "takyon_app_service_email_sends_today" in sql
    assert "takyon_app_visible_directory_entries" in sql
    assert "takyon_app_visible_directory_entry" in sql
    assert "security definer" in sql
    assert "p_session_hash" in sql
    assert "p_service_session_hash" in sql
    assert "source <> 'openmeter'" in sql
    assert "grant execute on function takyon_app_bind_supabase_session" in sql
    assert "grant execute on function takyon_app_validate_session" in sql
    assert "grant execute on function takyon_app_service_email_recipient" in sql
    assert "grant execute on function takyon_app_service_email_sends_today" in sql
    assert "grant execute on function takyon_app_visible_directory_entries" in sql
    assert "grant execute on function takyon_app_visible_directory_entry" in sql
    assert "grant execute on function takyon_app_record_event" in sql
    assert "grant execute on function takyon_app_media_usage" in sql
    assert "grant execute on function takyon_app_resolve_tier" not in sql
    assert "v_supabase_user_id uuid" in uuid_cast_sql
    assert "v_supabase_user_id := trim(p_supabase_user_id)::uuid" in uuid_cast_sql
    assert "u.supabase_user_id = v_supabase_user_id" in uuid_cast_sql
    assert "set supabase_user_id = v_supabase_user_id" in uuid_cast_sql
    assert "to takyon_app_runtime, takyon_app" in sql
    media_usage_body = media_usage_sql.split("as $$", 1)[1].split("$$;", 1)[0]
    assert "p_session_hash text" in media_usage_sql
    assert "p_app_user_id" not in media_usage_body
    assert "from app_sessions s" in media_usage_sql
    assert "join app_users u" in media_usage_sql
    assert "and s.token_hash = v_session_hash" in media_usage_sql
    assert "and s.revoked_at is null" in media_usage_sql
    assert "and s.expires_at > now()" in media_usage_sql
    assert "and u.status = 'active'" in media_usage_sql
    assert "raise exception 'app_session_required'" in media_usage_sql
    assert "revoke select on app_users, app_sessions\n    from takyon_app_runtime, takyon_app" in sql
    assert "v_scope <> ('business:' || v_business_slug || '/app')" in sql
    assert "v_event_type = '' or v_event_type not like 'app.%'" in sql
    assert "drop policy if exists takyon_app_media_write on app_media" in sql
    assert "app_user_id = coalesce(takyon_rls_effective_app_user_id()::text, '')" in sql
    assert "drop policy if exists takyon_app_checkout_intents_write on app_checkout_intents" in sql
    assert "app_user_id = takyon_rls_effective_app_user_id()" in sql
    assert "grant select on businesses" not in sql
    assert "grant insert" not in sql
    assert "grant update" not in sql
    assert "grant delete" not in sql
    for fn in (
        "takyon_app_account_entitlements",
        "takyon_app_account_usage_summary",
        "takyon_app_account_revenue_summary",
        "takyon_app_action_usage_limit",
    ):
        assert f"create or replace function {fn}" in money_sql
        assert "security definer" in money_sql
    assert "revoke select on app_entitlements, app_usage_events, app_revenue_events" in money_sql
    assert "p_session_hash" in money_sql
    assert "from app_sessions s" in money_sql
    assert "join app_users u" in money_sql
    assert "and s.token_hash = p_session_hash" in money_sql
    assert "and s.revoked_at is null" in money_sql
    assert "and s.expires_at > now()" in money_sql
    for fn in (
        "takyon_app_session_plan",
        "takyon_app_reserve_usage",
        "takyon_app_settle_usage",
        "takyon_app_release_usage",
    ):
        assert f"create or replace function {fn}" in usage_sql
        assert "security definer" in usage_sql
    assert "revoke execute on function safebox_reserve_usage" in usage_sql
    assert "revoke execute on function safebox_settle_usage" in usage_sql
    assert "revoke execute on function safebox_release_usage" in usage_sql
    assert "revoke execute on function safebox_reconcile_held_usage" in usage_sql
    assert "from takyon_app_runtime, takyon_app" in usage_sql
    assert "p_expected_app_user_id <> v_user_id" in usage_sql
    assert "e.reservation_key = p_reservation_key" in usage_sql
    assert "source <> 'openmeter'" in usage_sql
    assert "takyon_app_session_plan" in inspect.getsource(ai_gateway._resolve_plan_for_session)
    assert "takyon_app_reserve_usage" in inspect.getsource(app_usage.reserve_usage)
    assert "takyon_app_settle_usage" in inspect.getsource(app_usage.settle_usage)
    assert "takyon_app_release_usage" in inspect.getsource(app_usage.release_usage)
    assert "revoke select, insert, update, delete on app_checkout_sessions" in checkout_sql
    assert "from takyon_app_runtime, takyon_app" in checkout_sql

    assert "takyon_app_bind_supabase_session" in identity_src
    assert "_hash_token(raw_session)" in identity_src
    assert "raw_session" not in sql
    assert "takyon_app_validate_session" in inspect.getsource(app_identity.validate_session)
    assert "_hash_token(token)" in inspect.getsource(app_identity.validate_session)
    assert "takyon_app_revoke_session" in revoke_src
    assert "_hash_token(token)" in revoke_src
    assert "takyon_app_runtime_business" in inspect.getsource(core.TakyonStore._business)
    assert "takyon_app_control_blocker" in inspect.getsource(core.TakyonStore._control_blocker)
    assert "with store._pg_app_scope(conn, business, session_token=session_token)" in supabase_login_src
    assert "app_user_id=None if session_token else resolved.user.id" in profile_src
    assert "takyon_app_record_event" in inspect.getsource(core.TakyonStore._record_event)

    roles_sql = _AUTHORITY_SPLIT_ROLES_PATH.read_text(encoding="utf-8").lower()
    app_grant_block = roles_sql.split("-- operator runtime:", 1)[0]
    assert "app_sessions" not in app_grant_block
    assert "app_users" not in app_grant_block
    assert "app_budgets" not in sql.split("grant select on", 1)[1]


def test_app_plane_customer_write_handlers_do_not_use_generic_commit_path_first():
    """Product-host customer writes must run as app-plane leaf calls from the start.

    Operator/admin tools may still use the generic commit path outside the product app plane, but
    customer-session writes must fail before that path if the handler is not already on the app DB
    plane.
    """
    handlers = [
        core.handle_business_upsert_app_profile,
        core.handle_business_upsert_app_directory_entry,
        core.handle_business_disable_app_directory_entry,
        core.handle_business_act_on_app_connection,
        core.handle_business_upsert_app_record,
        core.handle_business_delete_app_record,
    ]
    for handler in handlers:
        src = inspect.getsource(handler)
        assert 'if store._database_plane == "app":' in src, handler.__name__
        assert "_require_app_plane_session_token(args)" in src, handler.__name__
        assert "session_token=session_token" in src, handler.__name__
        assert "_app_plane_customer_write_context" in src, handler.__name__
        assert "requires app-plane database login" in src, handler.__name__
        assert src.index('if store._database_plane == "app":') < src.index("_commit_tool_data"), handler.__name__


def test_app_plane_account_read_does_not_reconcile_checkout():
    src = inspect.getsource(core._maybe_reconcile_pg_completed_checkout)
    account_src = inspect.getsource(core.handle_business_read_app_account)
    money_sql = _APP_RUNTIME_MONEY_READ_PORTS_PATH.read_text(encoding="utf-8").lower()
    assert 'if store._database_plane == "app":' in src
    assert "app_plane_read_does_not_reconcile_checkout" in src
    assert "checkout_reconciliation_requires_safebox" in src
    assert "stripe_util" not in src
    assert src.index('if store._database_plane == "app":') < src.index("app_checkout_intents")
    account_validation_branch = account_src.split(
        'if isinstance(conn, _PGConn) and store._database_plane == "app":',
        1,
    )[1].split("\n            else:", 1)[0]
    account_summary_branch = account_src.split(
        'if isinstance(conn, _PGConn) and store._database_plane == "app":',
        2,
    )[2].split("\n            else:", 1)[0]
    assert "validate_session(leaf, business, session_token)" in account_validation_branch
    assert "app_sessions" not in account_validation_branch
    assert "SELECT * FROM app_users" not in account_validation_branch
    assert "takyon_app_account_entitlements" in account_summary_branch
    assert "takyon_app_account_usage_summary" in account_summary_branch
    assert "takyon_app_account_revenue_summary" in account_summary_branch
    assert "SELECT * FROM app_entitlements" not in account_summary_branch
    assert "FROM app_usage_events" not in account_summary_branch
    assert "FROM app_revenue_events" not in account_summary_branch
    assert "revoke select on app_entitlements, app_usage_events, app_revenue_events" in money_sql
    assert "_require_app_database_plane_for_pg(store, conn, action=\"app account session read\")" in account_src
    assert "else:\n                with (" in account_src


def test_app_plane_self_reported_usage_does_not_write_ledger():
    src = inspect.getsource(core.handle_business_record_app_usage)
    assert 'if store._database_plane == "app":' in src
    assert "priced app usage must flow through metered server brokers" in src
    assert "self_reported_app_usage_disabled_on_app_plane" in src
    assert src.index('if store._database_plane == "app":') < src.index("_commit_tool")


def test_app_media_binds_rls_scope_for_customer_row_writes():
    upload_src = inspect.getsource(core.handle_business_upload_app_media)
    delete_handler_src = inspect.getsource(core.handle_business_delete_app_media)
    store_src = inspect.getsource(app_media.store_media)
    uploader_src = inspect.getsource(app_media._resolve_uploader)
    insert_src = inspect.getsource(app_media._insert_media_row)
    read_src = inspect.getsource(app_media._media_row)
    delete_src = inspect.getsource(app_media._delete_media_row)
    session_src = inspect.getsource(app_media._session_user_id)
    usage_src = inspect.getsource(app_media._usage_totals)

    assert "validate_session(leaf, business, session_token)" in upload_src
    assert "app_user_email=user_email" in upload_src
    assert "app_user_tier=user_tier" in upload_src
    assert "if app_user_email or app_user_tier:" in store_src
    assert "session_token=session_token" in store_src
    assert "validate_session(leaf, business_slug, session_token)" in uploader_src
    assert "app_user_id=None if session_token else app_user_id" in uploader_src
    assert "_require_app_database_plane_for_pg(store, conn, action=\"app media upload\")" in upload_src
    assert "session_token=session_token" in insert_src
    assert "session_token=session_token" in read_src
    assert "session_token=session_token" in delete_src
    assert "app_user_id=None if session_token else app_user_id" in insert_src
    assert "app_user_id=None if session_token else app_user_id" in read_src
    assert "app_user_id=None if session_token else app_user_id" in delete_src
    assert "_pg_app_scope(conn, business_slug, session_token=session_token)" in session_src
    assert "_require_app_database_plane_for_pg" in insert_src
    assert "_require_app_database_plane_for_pg" in read_src
    assert "_require_app_database_plane_for_pg" in delete_src
    assert "_require_app_database_plane_for_pg" in session_src
    assert "takyon_app_media_usage" in usage_src
    assert "session_token=session_token" in usage_src
    assert "app_user_id=None if session_token else app_user_id" in usage_src
    assert "_hash_token(str(session_token))" in usage_src
    assert "session_token is required" in usage_src
    assert "_usage_totals(\n        store," in store_src
    assert "_insert_media_row(\n            store," in store_src
    assert "_app_actions._release_usage(" in store_src
    assert "_app_actions._settle_usage(" in store_src
    assert store_src.count("session_token=session_token") >= 4
    assert "session_token=session_token" in delete_handler_src


def test_app_directory_uses_visible_projection_ports_on_app_runtime():
    list_src = inspect.getsource(app_directory.list_visible_entries)
    read_src = inspect.getsource(app_directory.get_visible_entry)
    handler_src = inspect.getsource(core.handle_business_read_app_directory_entry)

    assert "app_identity._is_app_runtime_user(conn)" in list_src
    assert "takyon_app_visible_directory_entries" in list_src
    assert "app_identity._hash_token(session_token)" in list_src
    assert "app_identity._is_app_runtime_user(conn)" in read_src
    assert "takyon_app_visible_directory_entry" in read_src
    assert "target_email" in read_src
    assert "target_user = leaves[\"identity\"].get_app_user" not in handler_src


def test_app_connections_use_visible_target_port_on_app_runtime():
    setter_src = inspect.getsource(app_connections.set_connection)
    list_src = inspect.getsource(app_connections.list_connections)
    helper_src = inspect.getsource(app_connections._visible_target_for_session)

    assert "app_identity._is_app_runtime_user(conn)" in setter_src
    assert "_visible_target_for_session" in setter_src
    assert "normalized_action in {\"like\", \"pass\"}" in setter_src
    app_runtime_branch = setter_src.split(
        "if session_token is not None and app_identity._is_app_runtime_user(conn):",
        1,
    )[1].split("\n    else:\n        target = app_identity.get_app_user", 1)[0]
    assert "app_identity.get_app_user" not in app_runtime_branch
    assert "_target_placeholder" in app_runtime_branch
    assert "app_directory.get_visible_entry" in helper_src
    assert "session_token=session_token" in helper_src
    assert "if session_token is not None and app_identity._is_app_runtime_user(conn):" in list_src
    list_app_runtime_branch = list_src.split(
        "if session_token is not None and app_identity._is_app_runtime_user(conn):",
        1,
    )[1].split('\n    if list_state == "matches":', 1)[0]
    assert "app_users" not in list_app_runtime_branch
    assert "app_directory.get_visible_entry" in list_app_runtime_branch


def test_app_plane_checkout_uses_session_bound_user_and_safebox_stripe():
    src = inspect.getsource(core.handle_business_create_app_checkout)
    web_src = (Path(core.__file__).parents[2] / "takyon_cli" / "web_server.py").read_text(encoding="utf-8")

    assert 'if store._database_plane == "app":' in src
    assert "_require_app_plane_session_token(args)" in src
    assert "validate_session(raw, business, session_token)" in src
    assert "customer_email = str(user.email or \"\")" in src
    assert "app plan is not configured for Stripe checkout" in src
    assert "safebox.stripe_request(\"checkout/sessions\"" in src
    assert "_stripe_request(\"checkout/sessions\"" not in src
    assert "checkout_url = success_url" in src
    assert src.index('if store._database_plane == "app":') < src.index("safebox.stripe_request")
    assert 'raise TakyonError("app checkout requires app-plane database login")' in src
    assert src.index('"client_reference_id": client_reference_id,') < src.index(
        'raise TakyonError("app checkout requires app-plane database login")'
    )
    assert "\"session_token\": token" in web_src


def test_app_stripe_webhook_route_enters_app_database_plane():
    web_src = (Path(core.__file__).parents[2] / "takyon_cli" / "web_server.py").read_text(encoding="utf-8")
    route = web_src.split('@app.post("/api/webhooks/stripe")', 1)[1].split('@app.get("/healthz")', 1)[0]

    assert "with app_runtime_database_plane():" in route
    assert route.index("with app_runtime_database_plane():") < route.index("handle_business_record_stripe_webhook")


def test_safebox_app_checkout_recovery_requires_product_context_before_stripe():
    safebox_src = Path(core.__file__).with_name("safebox.py").read_text(encoding="utf-8")
    safebox_app_src = Path(core.__file__).with_name("safebox_app.py").read_text(encoding="utf-8")

    assert "checkout recovery requires expected business and app user/email context" in safebox_src
    assert "checkout_context_required" in safebox_app_src
    assert "if not expected_business or (not expected_user and not expected_email):" in safebox_app_src
    assert safebox_app_src.index("checkout_context_required") < safebox_app_src.index(
        'safebox.stripe_request(f"checkout/sessions/{session_id}"'
    )


def test_safebox_stripe_catalog_mutation_requires_operator_authority():
    safebox_src = Path(core.__file__).with_name("safebox.py").read_text(encoding="utf-8")
    safebox_app_src = Path(core.__file__).with_name("safebox_app.py").read_text(encoding="utf-8")

    assert '_STRIPE_CATALOG_MUTATION_PATHS = frozenset({"products", "prices"})' in safebox_app_src
    assert "path in _STRIPE_CATALOG_MUTATION_PATHS" in safebox_app_src
    assert "_require_operator_client(request)" in safebox_app_src
    assert 'stripe_path in {"products", "prices"}' in safebox_src
    assert "operator_authority=operator_authority" in safebox_src


def test_generic_stripe_route_does_not_read_billing_objects():
    safebox_app_src = Path(core.__file__).with_name("safebox_app.py").read_text(encoding="utf-8")
    normalize_src = inspect.getsource(safebox_app._normalize_stripe_request)

    assert "checkout/sessions" in normalize_src
    assert "products" in normalize_src and "prices" in normalize_src
    assert "subscriptions" not in normalize_src
    assert "stripe_method == \"GET\"" not in normalize_src
    assert 'safebox.stripe_request(f"checkout/sessions/{session_id}"' in safebox_app_src
    assert 'f"subscriptions/{subscription_id}"' in safebox_app_src


def test_app_plane_subscription_cancel_uses_safebox_authority():
    handler_src = inspect.getsource(core.handle_business_cancel_app_subscription)
    safebox_src = Path(core.__file__).with_name("safebox.py").read_text(encoding="utf-8")
    safebox_app_src = Path(core.__file__).with_name("safebox_app.py").read_text(encoding="utf-8")

    assert 'if store._database_plane == "app":' in handler_src
    assert "validate_session(leaf, business, session_token)" in handler_src
    assert "safebox.cancel_app_subscription" in handler_src
    assert "session_token=session_token" in handler_src
    assert "stripe_util" not in handler_src
    assert 'raise TakyonError("app subscription cancellation requires app-plane database login")' in handler_src
    assert "def cancel_app_subscription(" in safebox_src
    assert "session_token: str" in safebox_src
    assert '"session_token": token' in safebox_src
    assert "app_identity.validate_session(payment_conn, business, token)" in safebox_src
    assert "app_session_user_mismatch" in safebox_src
    assert '"/v1/stripe/app-subscription/cancel"' in safebox_src
    assert '@app.post("/v1/stripe/app-subscription/cancel")' in safebox_app_src
    assert "session_token: str | None = None" in safebox_app_src
    assert "app_identity.validate_session(conn, business, session_token)" in safebox_app_src
    assert "app_session_user_mismatch" in safebox_app_src
    assert safebox_app_src.index("app_identity.validate_session(conn, business, session_token)") < safebox_app_src.index(
        "app_payments.cancel_subscription"
    )
    assert "app_payments.cancel_subscription" in safebox_app_src


def test_app_plane_service_email_uses_service_session_ports():
    handler_src = inspect.getsource(core.handle_business_send_app_email)
    email_src = inspect.getsource(app_email)

    assert 'if getattr(store, "_database_plane", "") == "app":' in handler_src
    assert "_require_app_plane_session_token(args)" in handler_src
    assert "_app_plane_customer_write_context(store, conn, business, action=\"app.email.send\")" in handler_src
    assert "with store._pg_app_scope(conn, business, session_token=session_token)" in handler_src
    assert "validate_session(leaf, business, session_token)" in handler_src
    assert "service_session_token=session_token or None" in handler_src
    assert "takyon_app_service_email_recipient" in email_src
    assert "takyon_app_service_email_sends_today" in email_src
    assert "with store._pg_app_scope(conn, business_slug, session_token=service_session_token)" in email_src
    assert "safebox.send_postmark_email" in email_src
    assert "safebox.provider_broker_enabled()" in email_src
    assert "live service email requires the Safebox provider broker" in email_src
    assert "safebox.broker_provider_call(" in email_src
    assert '"recipient_app_user_id": recipient_app_user_id' in email_src
    assert '"to_email": to_email' not in email_src
    assert '"postmark.send"' in email_src


def test_safebox_postmark_product_email_uses_broker_not_legacy_route():
    safebox_src = Path(core.__file__).with_name("safebox.py").read_text(encoding="utf-8")
    safebox_app_src = Path(core.__file__).with_name("safebox_app.py").read_text(encoding="utf-8")

    assert '_POSTMARK_SEND_AUDIENCE = "postmark.send"' in safebox_app_src
    assert '("postmark", "send"): "/v1/providers/postmark/send"' in safebox_src
    assert '@app.post("/v1/providers/postmark/send")' in safebox_app_src
    assert "_postmark_authorize_service_send(" in safebox_app_src
    assert "recipient_app_user_id_required" in safebox_app_src
    assert '"to_email": resolved["recipient_email"]' in safebox_app_src
    assert 'ledger=_UsageLedgerAdapter(provider="postmark", purpose="email_send", route="email")' in safebox_app_src
    assert "@app.post(\"/v1/postmark/send\")" in safebox_app_src
    assert "_require_operator_client(request)" in safebox_app_src


def test_app_plane_action_invocation_binds_scope_before_runner():
    src = inspect.getsource(core.handle_business_invoke_app_action)
    reserve_limit_src = inspect.getsource(app_actions._resolve_pg_action_usage_limit)
    reserve_src = inspect.getsource(app_actions._reserve_usage)
    settle_src = inspect.getsource(app_actions._settle_usage)
    release_src = inspect.getsource(app_actions._release_usage)
    invoke_src = inspect.getsource(app_actions.invoke_action)

    assert 'if getattr(store, "_database_plane", "") == "app":' in src
    assert "session_token is required" in src
    assert "_app_plane_customer_write_context(store, conn, business, action=\"app.action.invoke\")" in src
    assert "with store._pg_app_scope(conn, business, session_token=session_token)" in src
    assert "validate_session(leaf, business, session_token)" in src
    assert src.index("with store._pg_app_scope(conn, business, session_token=session_token)") < src.index(
        "takyon_app_actions.invoke_action"
    )
    assert "takyon_app_action_usage_limit" in reserve_limit_src
    assert "_hash_token(token)" in reserve_limit_src
    assert "get_active_entitlement" in reserve_limit_src
    assert reserve_limit_src.index("takyon_app_action_usage_limit") < reserve_limit_src.index("get_active_entitlement")
    assert "session_token=session_token" in reserve_src
    assert 'settle_kwargs["session_token"] = session_token' in settle_src
    assert 'release_kwargs["session_token"] = session_token' in release_src
    assert "session_token=app_session_token" in invoke_src
    assert "app_session_token = str(principal.get(\"session_token\")" in invoke_src
    assert "get_app_user" not in reserve_limit_src


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
