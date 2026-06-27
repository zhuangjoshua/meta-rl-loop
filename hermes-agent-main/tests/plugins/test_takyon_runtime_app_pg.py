"""Postgres integration test for the runtime host app (``plugins/takyon/runtime_app.py``) — the real
mounting step.

Proves the connective tissue the routers deliberately omit: ``build_runtime_app()`` binds the
control router to a real psycopg connection against real Postgres, so a presented bearer key resolves
end-to-end through the SAME ``get_control_conn`` seam production uses — not a test-only override we
wrote in the test. This is the "it actually serves against Postgres" proof for the Runtime Cutover
connection layer.

Real engine on real Postgres (never mocks). Skips unless psycopg + fastapi are importable and
TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from psycopg.conninfo import make_conninfo  # noqa: E402

from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402
from plugins.takyon.runtime_app import (  # noqa: E402
    DatabaseAccessDenied,
    DatabaseRoleMismatch,
    RuntimeNotConfigured,
    assert_takyon_pg_role,
    build_runtime_app,
    resolve_database_url,
)


def _sub() -> str:
    return f"auth0|{uuid.uuid4().hex}"


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


class _RoleResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _RoleConn:
    def __init__(self, *, session_user: str, current_user: str):
        self.session_user = session_user
        self.current_user = current_user

    def execute(self, _sql, _params=None):
        return _RoleResult(
            {
                "session_user": self.session_user,
                "current_user": self.current_user,
            }
        )


def _app_route_paths(app) -> set[str]:
    paths: set[str] = set()
    for route in app.routes:
        path = str(getattr(route, "path", "") or "")
        if path:
            paths.add(path)
        original_router = getattr(route, "original_router", None)
        for child in getattr(original_router, "routes", []) if original_router is not None else []:
            child_path = str(getattr(child, "path", "") or "")
            if child_path:
                paths.add(child_path)
    return paths


@pytest.fixture
def client(pg_conn):
    # Point the HOST APP at the SAME throwaway DB the pg_conn fixture migrated, by overriding only
    # the dbname on the base DSN. The app then opens its OWN per-request connections there — so the
    # request path under test is the production seam, not the fixture's own connection.
    url = make_conninfo(os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname)
    app = build_runtime_app(database_url=url)
    return TestClient(app)


def test_healthz_is_live(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_me_resolves_through_real_pg_seam(client, pg_conn):
    # Provision a user (mints exactly one active key) in the throwaway DB; first login returns the
    # raw bearer key exactly once.
    uid, created, raw = provision_user_on_first_login(pg_conn, _sub())
    assert created is True
    assert raw

    # Resolve it through the host app's OWN per-request psycopg connection (a separate connection to
    # the same database) — the real end-to-end path, identity projection only.
    resp = client.get("/v1/me", headers=_auth(raw))
    assert resp.status_code == 200
    assert resp.json() == {"user_id": uid, "status": "active"}


def test_owned_business_resolves_through_real_pg_seam(client, pg_conn):
    # A second endpoint over the same seam, to prove the connection carries real owned-set state and
    # not just the identity row.
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    pg_conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", uid),
    )

    resp = client.get("/v1/businesses", headers=_auth(raw))
    assert resp.status_code == 200
    assert resp.json() == {"businesses": [{"slug": slug, "name": "Acme", "mode": "test"}]}


def test_missing_bearer_is_401(client):
    resp = client.get("/v1/me")
    assert resp.status_code == 401


def test_unknown_wellformed_key_is_401(client):
    # Structurally plausible tk_ key that was never minted → one undifferentiated 401.
    resp = client.get("/v1/me", headers=_auth("tk_" + "a" * 43))
    assert resp.status_code == 401


def test_build_without_database_url_raises(monkeypatch):
    # Invariant #8: no silent half-live server. With no DB configured anywhere, building refuses
    # loudly rather than starting a server that 500s every request. Needs no DB, so it runs even
    # where TAKYON_TEST_PG_DSN is unset.
    from plugins.takyon import safebox

    for name in (
        "DATABASE_URL",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
        "POSTGRES_URL_NON_POOLING",
        "TAKYON_OPERATOR_DATABASE_URL",
        "TAKYON_APP_DATABASE_URL",
        "TAKYON_SAFEBOX_DATABASE_URL",
        "TAKYON_MIGRATION_DATABASE_URL",
        "MIGRATION_DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("TAKYON_HOST_ROLE", raising=False)
    monkeypatch.setattr(safebox, "load_env", lambda: {})
    with pytest.raises(RuntimeNotConfigured):
        build_runtime_app()
    with pytest.raises(RuntimeNotConfigured):
        resolve_database_url()


def test_build_runtime_app_operator_host_mounts_only_operator_plane(monkeypatch):
    import plugins.takyon.runtime_app as runtime_app_mod

    calls: list[str | None] = []

    def _fake_resolve_database_url(explicit=None, *, plane=None):
        calls.append(plane)
        if plane == "app":
            raise AssertionError("operator host must not resolve the app-plane DSN")
        return "postgresql://operator.example.com/runtime"

    monkeypatch.setenv("TAKYON_HOST_ROLE", "operator")
    monkeypatch.setattr(runtime_app_mod, "resolve_database_url", _fake_resolve_database_url)

    app = runtime_app_mod.build_runtime_app()
    route_paths = _app_route_paths(app)

    assert calls == ["operator"]
    assert "/v1/me" in route_paths
    assert "/internal/creative-gateway/logo-render" in route_paths
    assert "/internal/ai-gateway/messages" not in route_paths


def test_build_runtime_app_subuser_host_mounts_only_app_plane(monkeypatch):
    import plugins.takyon.runtime_app as runtime_app_mod

    calls: list[str | None] = []

    def _fake_resolve_database_url(explicit=None, *, plane=None):
        calls.append(plane)
        if plane == "operator":
            raise AssertionError("subuser host must not resolve the operator-plane DSN")
        return "postgresql://app.example.com/runtime"

    monkeypatch.setenv("TAKYON_HOST_ROLE", "subuser")
    monkeypatch.setattr(runtime_app_mod, "resolve_database_url", _fake_resolve_database_url)

    app = runtime_app_mod.build_runtime_app()
    route_paths = _app_route_paths(app)

    assert calls == ["app"]
    assert "/internal/ai-gateway/messages" in route_paths
    assert "/v1/me" not in route_paths
    assert "/internal/creative-gateway/logo-render" not in route_paths


def test_resolve_database_url_blocks_macos_by_default(monkeypatch):
    monkeypatch.setattr("plugins.takyon.runtime_app.platform.system", lambda: "Darwin")
    monkeypatch.delenv("TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS", raising=False)

    with pytest.raises(DatabaseAccessDenied, match="blocked on macOS by default"):
        resolve_database_url(explicit="postgresql://localhost/testdb")


def test_resolve_database_url_allows_explicit_override(monkeypatch):
    monkeypatch.setattr("plugins.takyon.runtime_app.platform.system", lambda: "Darwin")
    monkeypatch.setenv("TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS", "1")

    assert resolve_database_url(explicit="postgresql://localhost/testdb") == "postgresql://localhost/testdb"


def test_resolve_database_url_allows_remote_on_approved_vps_runtime(monkeypatch):
    monkeypatch.setattr("plugins.takyon.runtime_app.platform.system", lambda: "Linux")
    monkeypatch.delenv("TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS", raising=False)
    monkeypatch.setenv("TAKYON_HOST_ROLE", "operator")
    monkeypatch.setenv("TAKYON_HOME", "/opt/takyon/.takyon")

    assert (
        resolve_database_url(explicit="postgresql://db.example.com/prod")
        == "postgresql://db.example.com/prod"
    )


def test_resolve_database_url_requires_plane_specific_dsn_on_host_role(monkeypatch):
    import plugins.takyon.runtime_app as runtime_app_mod
    from plugins.takyon import safebox

    runtime_app_mod.reset_database_url_cache()
    monkeypatch.setattr("plugins.takyon.runtime_app.platform.system", lambda: "Linux")
    monkeypatch.setenv("TAKYON_HOST_ROLE", "operator")
    monkeypatch.setenv("TAKYON_HOME", "/opt/takyon/.takyon")
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example.com/legacy")
    monkeypatch.delenv("TAKYON_OPERATOR_DATABASE_URL", raising=False)
    monkeypatch.setattr(safebox, "load_env", lambda: {})

    try:
        with pytest.raises(RuntimeNotConfigured, match="operator database URL"):
            resolve_database_url()
    finally:
        runtime_app_mod.reset_database_url_cache()


def test_resolve_database_url_uses_named_app_plane(monkeypatch):
    import plugins.takyon.runtime_app as runtime_app_mod
    from plugins.takyon import safebox

    runtime_app_mod.reset_database_url_cache()
    monkeypatch.setattr("plugins.takyon.runtime_app.platform.system", lambda: "Linux")
    monkeypatch.setenv("TAKYON_HOST_ROLE", "subuser")
    monkeypatch.setenv("TAKYON_HOME", "/opt/takyon/.takyon")
    monkeypatch.delenv("TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS", raising=False)
    monkeypatch.setenv("TAKYON_APP_DATABASE_URL", "postgresql://db.example.com/app")
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example.com/operator")
    monkeypatch.setattr(safebox, "load_env", lambda: {})

    try:
        assert resolve_database_url() == "postgresql://db.example.com/app"
        assert resolve_database_url(plane="app") == "postgresql://db.example.com/app"
    finally:
        runtime_app_mod.reset_database_url_cache()


def test_resolve_database_url_memoises_the_local_env_lookup(monkeypatch):
    import plugins.takyon.runtime_app as runtime_app_mod
    from plugins.takyon import safebox

    runtime_app_mod.reset_database_url_cache()
    monkeypatch.setattr("plugins.takyon.runtime_app.platform.system", lambda: "Linux")
    monkeypatch.setenv("TAKYON_HOST_ROLE", "operator")
    monkeypatch.setenv("TAKYON_HOME", "/opt/takyon/.takyon")
    monkeypatch.delenv("TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS", raising=False)
    monkeypatch.setenv("TAKYON_OPERATOR_DATABASE_URL", "postgresql://db.example.com/prod")

    calls = {"n": 0}

    def _counting_load_env():
        calls["n"] += 1
        return {}

    monkeypatch.setattr(safebox, "load_env", _counting_load_env)

    try:
        first = resolve_database_url()
        second = resolve_database_url()
        assert first == second == "postgresql://db.example.com/prod"
        assert calls["n"] == 1, "DB-URL env lookup must be memoised once per process"
    finally:
        runtime_app_mod.reset_database_url_cache()


def test_assert_takyon_pg_role_accepts_direct_app_runtime_role():
    assert assert_takyon_pg_role(
        _RoleConn(session_user="takyon_app_runtime", current_user="takyon_app_runtime"),
        "app",
    ) == ("takyon_app_runtime", "takyon_app_runtime")


def test_assert_takyon_pg_role_rejects_legacy_app_role_by_default(monkeypatch):
    monkeypatch.delenv("TAKYON_ALLOW_LEGACY_DB_ROLES", raising=False)
    with pytest.raises(DatabaseRoleMismatch, match="app database role mismatch"):
        assert_takyon_pg_role(
            _RoleConn(session_user="takyon_app", current_user="takyon_app"),
            "app",
        )


def test_assert_takyon_pg_role_accepts_legacy_roles_only_with_explicit_cutover_opt_in(monkeypatch):
    monkeypatch.setenv("TAKYON_ALLOW_LEGACY_DB_ROLES", "1")
    assert assert_takyon_pg_role(
        _RoleConn(session_user="takyon_app", current_user="takyon_app"),
        "app",
    ) == ("takyon_app", "takyon_app")
    assert assert_takyon_pg_role(
        _RoleConn(session_user="takyon_runtime", current_user="takyon_runtime"),
        "operator",
    ) == ("takyon_runtime", "takyon_runtime")
    assert assert_takyon_pg_role(
        _RoleConn(session_user="postgres", current_user="postgres"),
        "safebox",
    ) == ("postgres", "postgres")


def test_assert_takyon_pg_role_rejects_demoted_operator_session_for_app():
    with pytest.raises(DatabaseRoleMismatch, match="app database role mismatch"):
        assert_takyon_pg_role(
            _RoleConn(session_user="takyon_runtime", current_user="takyon_app"),
            "app",
        )


def test_assert_takyon_pg_role_rejects_app_current_user_for_operator():
    with pytest.raises(DatabaseRoleMismatch, match="operator database role mismatch"):
        assert_takyon_pg_role(
            _RoleConn(session_user="takyon_runtime", current_user="takyon_app"),
            "operator",
        )
