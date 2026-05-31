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

import psycopg
from fastapi import FastAPI

from .ai_gateway import build_ai_gateway_router, get_gateway_conn
from .control_api import build_control_router, get_control_conn

# DATABASE_URL is canonical; POSTGRES_URL / POSTGRES_PRISMA_URL are the platform-managed aliases
# (Supabase / Vercel). Kept identical to core.py's "database" provider aliases on purpose, so one
# deploy variable feeds both the SQLite-era config reader and this Postgres host.
_DATABASE_URL_ENV = ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL")


class RuntimeNotConfigured(RuntimeError):
    """Raised when the runtime host is built without any database URL (invariant #8: blocked with a
    reason, never a silent fallback)."""


def resolve_database_url(explicit: str | None = None) -> str:
    """The configured Postgres URL: an explicit argument wins (tests point it at a throwaway DB),
    else the first non-empty of DATABASE_URL / POSTGRES_URL / POSTGRES_PRISMA_URL. Absent
    everywhere → ``RuntimeNotConfigured``."""
    if explicit and explicit.strip():
        return explicit
    for name in _DATABASE_URL_ENV:
        value = os.environ.get(name)
        if value and value.strip():
            return value
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
        conn = psycopg.connect(resolved_url, autocommit=True)
        try:
            yield conn
        finally:
            conn.close()

    app.include_router(build_control_router())
    app.include_router(build_ai_gateway_router())
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
