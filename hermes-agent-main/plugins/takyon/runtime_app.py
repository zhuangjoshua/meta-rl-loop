"""Host FastAPI app that actually serves the Postgres-backed control plane.

The router modules (``control_api.py`` and the ``/internal`` AI gateway today; the rest of the App
Runtime API as it lands) are deliberately strategy-free: each exposes a ``build_*_router()`` factory
plus a ``get_*_conn`` dependency seam that raises until a host overrides it, and each says in its own
docstring that "mounting … is a separate, deliberate step." THIS is that step. ``build_runtime_app``
reads the database URL, opens a per-request psycopg connection, and overrides each seam so the
routers run against real Postgres — the same code path tests exercise, now bound to a live DB. The AI
gateway's provider-call seam is left at its production default, which resolves the real shared
provider key server-side; generated apps reach it only with their own ``tkg_`` gateway key.

It is intentionally the ONLY module that knows the production connection strategy, so the routers
stay identically testable and free of pool/connect concerns. Building or serving this app does NOT
by itself retire the SQLite runtime: flipping the live runtime onto it is the separate,
operator-gated Runtime Cutover step (mediationplan.md).

Invariant #8 (no silent fallback): with no database URL configured, building the app raises
``RuntimeNotConfigured`` loudly — we never start a half-live server that would 500 on every request,
and we never quietly fall back to SQLite.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

from .ai_gateway import build_ai_gateway_router, get_gateway_conn
from .control_api import build_control_router, get_control_conn
from .creative_gateway import build_creative_gateway_router
from . import environment, safebox

# Production database authority is plane-specific. Generic DATABASE_URL remains accepted only for
# explicit test/maintenance calls and local/unset host-role compatibility; a production host role must
# resolve its own named DSN so operator, app, Safebox, and migration authority cannot blur together.
_LEGACY_DATABASE_URL_ENV = ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL")
_DATABASE_PLANE_ENV: dict[str, tuple[str, ...]] = {
    "operator": ("TAKYON_OPERATOR_DATABASE_URL",),
    "app": ("TAKYON_APP_DATABASE_URL",),
    "safebox": ("TAKYON_SAFEBOX_DATABASE_URL",),
    "migration": ("TAKYON_MIGRATION_DATABASE_URL", "MIGRATION_DATABASE_URL"),
}
_DATABASE_PLANE_ALIASES = {
    "": "",
    "operator": "operator",
    "dashboard": "operator",
    "worker": "operator",
    "control": "operator",
    "app": "app",
    "subuser": "app",
    "product": "app",
    "customer": "app",
    "safebox": "safebox",
    "authority": "safebox",
    "migration": "migration",
    "migrate": "migration",
    "deploy": "migration",
}
_DEFAULT_CONNECT_TIMEOUT_SECONDS = 10
_ALLOW_POSTGRES_OUTSIDE_VPS_ENV = "TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS"
_ALLOW_LEGACY_DB_ROLES_ENV = "TAKYON_ALLOW_LEGACY_DB_ROLES"
_CONNECT_TIMEOUT_ENV = "TAKYON_POSTGRES_CONNECT_TIMEOUT_SECONDS"
_APPROVED_REMOTE_POSTGRES_HOST_ROLES = frozenset({"operator", "subuser", "safebox"})
_APPROVED_REMOTE_POSTGRES_HOME_PREFIXES = (Path("/opt/takyon/.takyon"),)
_LOOPBACK_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
class RuntimeNotConfigured(RuntimeError):
    """Raised when the runtime host is built without any database URL (invariant #8: blocked with a
    reason, never a silent fallback)."""


class DatabaseAccessDenied(RuntimeError):
    """Raised when a Takyon process tries to open Postgres from an unapproved host/runtime."""


class DatabaseRoleMismatch(RuntimeError):
    """Raised when a Postgres connection is not using the expected authority plane role."""


_DATABASE_PLANE_ROLES: dict[str, tuple[str, ...]] = {
    "operator": ("takyon_operator_runtime",),
    "app": ("takyon_app_runtime",),
    "safebox": ("takyon_safebox_authority",),
    "migration": ("takyon_migration",),
}
_LEGACY_DATABASE_PLANE_ROLES: dict[str, tuple[str, ...]] = {
    # Temporary cutover roles only. They preserve old deployments when explicitly opted in, but are
    # not accepted by default because they keep the old mixed-session model reachable.
    "operator": ("takyon_runtime",),
    "app": ("takyon_app",),
    "safebox": ("postgres",),
    "migration": ("postgres",),
}


def _env_truthy(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _database_plane_roles(plane: str) -> tuple[str, ...]:
    roles = _DATABASE_PLANE_ROLES.get(plane, ())
    if _env_truthy(_ALLOW_LEGACY_DB_ROLES_ENV):
        roles = roles + _LEGACY_DATABASE_PLANE_ROLES.get(plane, ())
    return roles


def _normalized_host_role() -> str:
    # Thin shim over the one role truth table (Stage 3): runtime_app uses the canonical view.
    return environment.HostRole.canonical()


def _normalize_database_plane(plane: str | None) -> str:
    raw = str(plane or "").strip().lower()
    return _DATABASE_PLANE_ALIASES.get(raw, raw)


def _database_plane_from_host_role() -> str:
    role = _normalized_host_role()
    if role == "operator":
        return "operator"
    if role == "subuser":
        return "app"
    if role == "safebox":
        return "safebox"
    return ""


def _runtime_planes_from_host_role() -> tuple[str, ...]:
    role = _normalized_host_role()
    if role == "operator":
        return ("operator",)
    if role == "subuser":
        return ("app",)
    return ("operator", "app")


def _normalize_runtime_planes(planes: tuple[str, ...] | list[str] | set[str] | None) -> tuple[str, ...]:
    if planes is None:
        return _runtime_planes_from_host_role()
    normalized: list[str] = []
    for plane in planes:
        value = _normalize_database_plane(plane)
        if value not in {"operator", "app"}:
            raise RuntimeNotConfigured(f"unknown runtime app plane: {plane}")
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise RuntimeNotConfigured("at least one runtime app plane is required")
    return tuple(normalized)


def _resolved_takyon_home() -> Path | None:
    raw = str(os.getenv("TAKYON_HOME") or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except Exception:
        return None


def _conninfo_hosts(value: str) -> tuple[str, ...]:
    from psycopg.conninfo import conninfo_to_dict

    try:
        info = conninfo_to_dict(value)
    except Exception:
        return ()
    hosts: list[str] = []
    for key in ("host", "hostaddr"):
        raw = str(info.get(key) or "").strip()
        if not raw:
            continue
        for part in raw.split(","):
            host = part.strip().strip("[]").lower()
            if host:
                hosts.append(host)
    return tuple(hosts)


def _conninfo_is_local_only(value: str) -> bool:
    hosts = _conninfo_hosts(value)
    if not hosts:
        # libpq with no host means a local Unix socket on the current machine.
        return True
    for host in hosts:
        if host in _LOOPBACK_DB_HOSTS:
            continue
        if host.startswith("/"):
            continue
        return False
    return True


def _with_database_connect_timeout(value: str) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    try:
        info = conninfo_to_dict(value)
    except Exception:
        return value
    if str(info.get("connect_timeout") or "").strip():
        return value
    raw_timeout = str(
        os.getenv(_CONNECT_TIMEOUT_ENV) or _DEFAULT_CONNECT_TIMEOUT_SECONDS
    ).strip()
    try:
        timeout = max(1, int(float(raw_timeout)))
    except (TypeError, ValueError):
        timeout = _DEFAULT_CONNECT_TIMEOUT_SECONDS
    return make_conninfo(value, connect_timeout=str(timeout))


def _approved_remote_postgres_runtime() -> bool:
    if platform.system().lower() != "linux":
        return False
    if _normalized_host_role() not in _APPROVED_REMOTE_POSTGRES_HOST_ROLES:
        return False
    home = _resolved_takyon_home()
    if home is None:
        return False
    return any(home == prefix or prefix in home.parents for prefix in _APPROVED_REMOTE_POSTGRES_HOME_PREFIXES)


def _enforce_database_url_policy(value: str) -> str:
    value = _with_database_connect_timeout(value)
    if _env_truthy(_ALLOW_POSTGRES_OUTSIDE_VPS_ENV):
        return value
    system = platform.system().lower()
    if system == "darwin":
        raise DatabaseAccessDenied(
            "Postgres access is blocked on macOS by default; use the VPS-hosted Takyon runtime, "
            f"or set {_ALLOW_POSTGRES_OUTSIDE_VPS_ENV}=1 for an intentional local override"
        )
    if _conninfo_is_local_only(value):
        return value
    if _approved_remote_postgres_runtime():
        return value
    raise DatabaseAccessDenied(
        "Remote Postgres access is blocked outside approved Takyon VPS runtimes; "
        f"role={_normalized_host_role() or 'unset'} takyon_home={_resolved_takyon_home() or 'unset'} "
        f"platform={platform.system()} host={','.join(_conninfo_hosts(value)) or 'unknown'}. "
        f"Use {_ALLOW_POSTGRES_OUTSIDE_VPS_ENV}=1 only for an intentional override."
    )


def configure_takyon_pg_session(conn, *, bypass: bool = True) -> None:
    """Initialize the Takyon app-plane RLS GUCs on an already-open psycopg connection.

    All four GUCs are set in a SINGLE statement/round-trip. Every store connection runs this on
    open, and the live DATABASE_URL is Supabase's remote pgbouncer (~20ms/round-trip), so issuing
    four separate ``set_config`` calls cost ~80ms of pure latency per connection — multiplied
    across the many short-lived connections a dashboard render opens. One combined SELECT keeps the
    exact same per-key semantics (local=false, session scope) at a quarter of the round-trips."""
    conn.execute(
        "select"
        " set_config('takyon.rls_bypass', %s, false),"
        " set_config('takyon.rls_business_slug', '', false),"
        " set_config('takyon.rls_app_user_id', '', false),"
        " set_config('takyon.rls_session_hash', '', false)",
        ("1" if bypass else "0",),
    )


def _row_value(row, index: int, key: str) -> str:
    if row is None:
        return ""
    if isinstance(row, dict):
        return str(row.get(key) or "").strip()
    try:
        return str(row[key] or "").strip()
    except Exception:
        try:
            return str(row[index] or "").strip()
        except Exception:
            return ""


def current_takyon_pg_roles(conn) -> tuple[str, str]:
    row = conn.execute("select session_user::text as session_user, current_user::text as current_user").fetchone()
    return _row_value(row, 0, "session_user"), _row_value(row, 1, "current_user")


def assert_takyon_pg_role(conn, plane: str) -> tuple[str, str]:
    """Fail closed if a connection is not on the expected DB authority plane.

    This checks BOTH session_user and current_user. That is deliberate: the observed production
    failure was exactly a pooled operator-capable session demoted to current_user=takyon_app.
    Checking current_user alone would accept that bad state on the app side and miss the leak on the
    operator side.
    """
    database_plane = _normalize_database_plane(plane)
    allowed = _database_plane_roles(database_plane)
    if not allowed:
        raise DatabaseRoleMismatch(f"unknown database authority plane: {database_plane or plane}")
    session_user, current_user = current_takyon_pg_roles(conn)
    if session_user in allowed and current_user in allowed:
        return session_user, current_user
    raise DatabaseRoleMismatch(
        f"{database_plane} database role mismatch: "
        f"session_user={session_user or 'unknown'} current_user={current_user or 'unknown'} "
        f"allowed={','.join(allowed)}"
    )


# Process-static memo of resolved no-explicit DB-URL env values (see resolve_database_url).
# Keyed by (environment.cache_scope(), plane cache key) — plan R3: a dev-scoped instance must
# never read a prod-scoped memoised DSN out of this process-global map.
_resolved_database_url_env_values: dict[tuple[str, str], str] = {}


def reset_database_url_cache() -> None:
    """Clear the process-static DB-URL memo. For tests; a rotated URL is picked up on restart
    (which every deploy performs), so no runtime invalidation is needed."""
    _resolved_database_url_env_values.clear()


def _first_configured_database_url(env_names: tuple[str, ...], *, cache_key: str) -> str:
    scoped_key = (environment.cache_scope(), cache_key)
    value = _resolved_database_url_env_values.get(scoped_key, "")
    if not value:
        try:
            env_values = safebox.load_env()
        except OSError:
            env_values = {}
        for name in env_names:
            value = str(os.environ.get(name) or env_values.get(name) or "").strip()
            if value:
                break
        if value:
            _resolved_database_url_env_values[scoped_key] = value
    return value


def resolve_database_url(explicit: str | None = None, *, plane: str | None = None) -> str:
    """Resolve the configured Postgres URL for one authority plane.

    Explicit arguments win for tests and intentional one-off maintenance. Otherwise production host
    roles resolve only their named plane DSN:

    * operator/dashboard/worker -> TAKYON_OPERATOR_DATABASE_URL
    * product app/sub-user -> TAKYON_APP_DATABASE_URL
    * Safebox -> TAKYON_SAFEBOX_DATABASE_URL
    * migrations/deploy -> TAKYON_MIGRATION_DATABASE_URL

    The no-explicit env lookup is memoised process-wide. DB URLs are intentionally read from this
    process' own environment/Takyon env file, not from the Safebox /v1/env HTTP egress path: possession
    of the shared Safebox transport token must not let one plane ask for another plane's DSN. Only the
    non-empty resolved value is cached, so "no DB configured -> raise" is unchanged and the policy gate
    still runs on every call; a restart picks up a rotated URL."""
    if explicit and explicit.strip():
        return _enforce_database_url_policy(explicit)

    database_plane = _normalize_database_plane(plane) or _database_plane_from_host_role()
    if database_plane:
        env_names = _DATABASE_PLANE_ENV.get(database_plane)
        if env_names is None:
            raise RuntimeNotConfigured(f"unknown database authority plane: {database_plane}")
        value = _first_configured_database_url(env_names, cache_key=f"plane:{database_plane}")
        if value:
            return _enforce_database_url_policy(value)
        raise RuntimeNotConfigured(
            f"no {database_plane} database URL configured; set {env_names[0]}"
        )

    value = _first_configured_database_url(_LEGACY_DATABASE_URL_ENV, cache_key="legacy")
    if value:
        return _enforce_database_url_policy(value)
    raise RuntimeNotConfigured(
        "no database URL configured; set DATABASE_URL "
        "(or POSTGRES_URL / POSTGRES_PRISMA_URL)"
    )


def build_runtime_app(
    *,
    database_url: str | None = None,
    operator_database_url: str | None = None,
    app_database_url: str | None = None,
    planes: tuple[str, ...] | list[str] | set[str] | None = None,
) -> FastAPI:
    """Build the host app that serves the Postgres-backed routers against ``database_url`` (or the
    environment). Raises ``RuntimeNotConfigured`` if no URL is configured."""
    import psycopg
    from fastapi import FastAPI

    runtime_planes = _normalize_runtime_planes(planes)
    mount_operator = "operator" in runtime_planes
    mount_app = "app" in runtime_planes
    resolved_operator_url = (
        resolve_database_url(
            operator_database_url or database_url,
            plane="operator" if not database_url else None,
        )
        if mount_operator
        else ""
    )
    resolved_app_url = (
        resolve_database_url(
            app_database_url or database_url,
            plane="app" if not database_url else None,
        )
        if mount_app
        else ""
    )

    app = FastAPI(title="Takyon Runtime")

    enforce_operator_role = not (database_url or operator_database_url)
    enforce_app_role = not (database_url or app_database_url)

    def _connect(url: str, *, bypass: bool, plane: str, enforce_role: bool):
        # One psycopg connection per request, autocommit=True to mirror exactly how every leaf is
        # tested and used: read paths need no transaction, and each mutating leaf op opens its own
        # `with conn.transaction():`. FastAPI caches this dependency per-request (use_cache), so the
        # SAME connection is reused across _resolve_principal → _rate_limited_principal → endpoint
        # within one request, then closed here when the request ends.
        #
        # prepare_threshold=None disables psycopg's automatic server-side prepared statements. The
        # live control-plane DATABASE_URL is Supabase's pgbouncer endpoint (port 6543); in
        # transaction-pooling mode a server backend is reassigned per transaction, so a statement
        # PREPAREd on one backend can EXECUTE on another that never saw it ("prepared statement
        # does not exist"). Short-lived per-request connections rarely cross the default threshold
        # (=5), but disabling auto-prepare removes the entire failure class regardless of pooler
        # mode. Correctness is identical (extended protocol either way); only a micro perf hint is
        # dropped, which a low-QPS control plane does not need.
        conn = psycopg.connect(url, autocommit=True, prepare_threshold=None)
        try:
            configure_takyon_pg_session(conn, bypass=bypass)
            if enforce_role:
                assert_takyon_pg_role(conn, plane)
            yield conn
        finally:
            conn.close()

    def control_conn():
        yield from _connect(
            resolved_operator_url,
            bypass=True,
            plane="operator",
            enforce_role=enforce_operator_role,
        )

    def app_conn():
        yield from _connect(
            resolved_app_url,
            bypass=False,
            plane="app",
            enforce_role=enforce_app_role,
        )

    if mount_operator:
        app.include_router(build_control_router())
        app.include_router(build_creative_gateway_router())
        app.dependency_overrides[get_control_conn] = control_conn
    if mount_app:
        app.include_router(build_ai_gateway_router())
        app.dependency_overrides[get_gateway_conn] = app_conn

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        # Liveness only: confirms the app is mounted and serving. It deliberately does NOT touch
        # Postgres — a DB round-trip belongs in a readiness probe that can be gated/alerted
        # separately, not on the hot liveness path.
        return {"status": "ok"}

    return app
