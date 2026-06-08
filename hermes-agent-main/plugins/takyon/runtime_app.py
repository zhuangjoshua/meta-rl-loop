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

import psycopg
from psycopg.conninfo import conninfo_to_dict
from fastapi import FastAPI

from .ai_gateway import build_ai_gateway_router, get_gateway_conn
from .control_api import build_control_router, get_control_conn
from .creative_gateway import build_creative_gateway_router
from . import safebox

# DATABASE_URL is canonical; POSTGRES_URL / POSTGRES_PRISMA_URL are the platform-managed aliases
# (Supabase / Vercel). Kept identical to core.py's "database" provider aliases on purpose, so one
# deploy variable feeds both the SQLite-era config reader and this Postgres host.
_DATABASE_URL_ENV = ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL")
_ALLOW_POSTGRES_OUTSIDE_VPS_ENV = "TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS"
_HOST_ROLE_ENV = "TAKYON_HOST_ROLE"
_HOST_ROLE_ALIASES = {
    "": "",
    "all": "combined",
    "combined": "combined",
    "default": "combined",
    "operator": "operator",
    "dashboard": "operator",
    "subuser": "subuser",
    "app": "subuser",
    "product": "subuser",
    "safebox": "safebox",
}
_APPROVED_REMOTE_POSTGRES_HOST_ROLES = frozenset({"operator", "subuser", "safebox"})
_APPROVED_REMOTE_POSTGRES_HOME_PREFIXES = (Path("/opt/takyon/.takyon"),)
_LOOPBACK_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class RuntimeNotConfigured(RuntimeError):
    """Raised when the runtime host is built without any database URL (invariant #8: blocked with a
    reason, never a silent fallback)."""


class DatabaseAccessDenied(RuntimeError):
    """Raised when a Takyon process tries to open Postgres from an unapproved host/runtime."""


def _env_truthy(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalized_host_role() -> str:
    raw = str(os.getenv(_HOST_ROLE_ENV) or "").strip().lower()
    return _HOST_ROLE_ALIASES.get(raw, raw)


def _resolved_takyon_home() -> Path | None:
    raw = str(os.getenv("TAKYON_HOME") or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except Exception:
        return None


def _conninfo_hosts(value: str) -> tuple[str, ...]:
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


def resolve_database_url(explicit: str | None = None) -> str:
    """The configured Postgres URL: an explicit argument wins (tests point it at a throwaway DB),
    else the first non-empty of DATABASE_URL / POSTGRES_URL / POSTGRES_PRISMA_URL. Absent
    everywhere → ``RuntimeNotConfigured``."""
    if explicit and explicit.strip():
        return _enforce_database_url_policy(explicit)
    try:
        value = safebox.first_env_backed_value(*_DATABASE_URL_ENV)
    except safebox.SafeboxAuthorityUnavailable:
        value = ""
    if value:
        return _enforce_database_url_policy(value)
    raise RuntimeNotConfigured(
        "no database URL configured; set DATABASE_URL "
        "(or POSTGRES_URL / POSTGRES_PRISMA_URL)"
    )


def build_runtime_app(*, database_url: str | None = None) -> FastAPI:
    """Build the host app that serves the Postgres-backed routers against ``database_url`` (or the
    environment). Raises ``RuntimeNotConfigured`` if no URL is configured."""
    resolved_url = resolve_database_url(database_url)

    app = FastAPI(title="Takyon Runtime")

    def control_conn():
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
        conn = psycopg.connect(resolved_url, autocommit=True, prepare_threshold=None)
        try:
            yield conn
        finally:
            conn.close()

    app.include_router(build_control_router())
    app.include_router(build_ai_gateway_router())
    app.include_router(build_creative_gateway_router())
    # Both routers open the SAME kind of per-request connection to the SAME database, so one
    # connection factory serves both seams. get_provider_caller is deliberately NOT overridden:
    # its default resolves the real shared provider key server-side (invariant #8 blocks when none
    # is configured), which is exactly the production behavior — only tests override it.
    app.dependency_overrides[get_control_conn] = control_conn
    app.dependency_overrides[get_gateway_conn] = control_conn

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        # Liveness only: confirms the app is mounted and serving. It deliberately does NOT touch
        # Postgres — a DB round-trip belongs in a readiness probe that can be gated/alerted
        # separately, not on the hot liveness path.
        return {"status": "ok"}

    return app
