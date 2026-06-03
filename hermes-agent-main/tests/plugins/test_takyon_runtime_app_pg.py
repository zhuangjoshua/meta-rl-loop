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
    RuntimeNotConfigured,
    build_runtime_app,
    resolve_database_url,
)


def _sub() -> str:
    return f"auth0|{uuid.uuid4().hex}"


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


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

    for name in ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(safebox, "load_env", lambda: {})
    with pytest.raises(RuntimeNotConfigured):
        build_runtime_app()
    with pytest.raises(RuntimeNotConfigured):
        resolve_database_url()


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
