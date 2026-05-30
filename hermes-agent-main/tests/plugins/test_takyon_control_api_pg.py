"""Postgres integration tests for the Control API read path — the opaque
Takyon-user HTTP boundary (Phase 1 acceptance: a request resolves to exactly one
user + their businesses before any privileged work; revoked/unknown keys are
rejected; one tenant can't read another's businesses).

Exercises the REAL FastAPI request path (resolver + DB), not mocks. Uses the shared
`pg_conn` fixture (per-worker throwaway DB); skips unless psycopg AND fastapi are
importable and TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from plugins.takyon.control_api import build_control_router, get_control_conn  # noqa: E402
from plugins.takyon.control_plane import (  # noqa: E402
    provision_user_on_first_login,
    resolve_api_key,
)


def _sub() -> str:
    return f"auth0|{uuid.uuid4().hex}"


def _add_business(conn, owner_id, name="Acme") -> str:
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, name, owner_id),
    )
    return slug


@pytest.fixture
def client(pg_conn):
    app = FastAPI()
    app.include_router(build_control_router())
    app.dependency_overrides[get_control_conn] = lambda: pg_conn
    return TestClient(app)


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def test_me_requires_bearer(client):
    resp = client.get("/v1/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing_bearer_token"
    assert resp.headers.get("WWW-Authenticate") == "Bearer"


def test_me_rejects_garbage_bearer(client):
    resp = client.get("/v1/me", headers=_auth("not-a-key"))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_api_key"


def test_me_rejects_unknown_but_wellformed_key(client, pg_conn):
    from plugins.takyon.user_api_keys import generate_api_key

    resp = client.get("/v1/me", headers=_auth(generate_api_key()))
    assert resp.status_code == 401


def test_me_rejects_revoked_key(client, pg_conn):
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    pg_conn.execute(
        "update user_api_keys set revoked_at = now() where user_id = %s", (uid,)
    )
    resp = client.get("/v1/me", headers=_auth(raw))
    assert resp.status_code == 401


def test_me_returns_resolved_identity(client, pg_conn):
    uid, created, raw = provision_user_on_first_login(pg_conn, _sub())
    assert created is True
    resp = client.get("/v1/me", headers=_auth(raw))
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"user_id": uid, "status": "active"}


def test_businesses_lists_only_owned(client, pg_conn):
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    mine = {_add_business(pg_conn, uid), _add_business(pg_conn, uid)}
    # a second user with their own business must not leak into the first's list
    other_uid, _, _ = provision_user_on_first_login(pg_conn, _sub())
    _add_business(pg_conn, other_uid, name="NotMine")

    resp = client.get("/v1/businesses", headers=_auth(raw))
    assert resp.status_code == 200
    slugs = {b["slug"] for b in resp.json()["businesses"]}
    assert slugs == mine


def test_business_detail_happy_path(client, pg_conn):
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    slug = _add_business(pg_conn, uid)
    resp = client.get(f"/v1/businesses/{slug}", headers=_auth(raw))
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == slug
    assert body["mode"] == "test"  # safe default


def test_business_detail_cross_tenant_is_404(client, pg_conn):
    owner_uid, _, _ = provision_user_on_first_login(pg_conn, _sub())
    secret_slug = _add_business(pg_conn, owner_uid, name="Confidential")
    # a different user, holding a valid key, must not be able to read it — and must
    # not even learn it exists, so the answer is 404, not 403.
    other_uid, _, other_raw = provision_user_on_first_login(pg_conn, _sub())
    resp = client.get(f"/v1/businesses/{secret_slug}", headers=_auth(other_raw))
    assert resp.status_code == 404


def test_jit_provision_is_idempotent_and_mints_once(pg_conn):
    sub = _sub()
    uid1, created1, raw1 = provision_user_on_first_login(pg_conn, sub, "a@example.com")
    uid2, created2, raw2 = provision_user_on_first_login(pg_conn, sub, "a@example.com")
    assert created1 is True and raw1 is not None
    assert created2 is False and raw2 is None
    assert uid1 == uid2
    # the once-minted key resolves to the same user
    principal = resolve_api_key(pg_conn, raw1)
    assert principal is not None and principal.user_id == uid1
    # exactly one active key
    active = pg_conn.execute(
        "select count(*) from user_api_keys where user_id = %s and revoked_at is null",
        (uid1,),
    ).fetchone()[0]
    assert active == 1


def test_read_path_runs_resolver_and_stamps_last_used(client, pg_conn):
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    before = pg_conn.execute(
        "select last_used_at from user_api_keys where user_id = %s", (uid,)
    ).fetchone()[0]
    assert before is None
    assert client.get("/v1/me", headers=_auth(raw)).status_code == 200
    after = pg_conn.execute(
        "select last_used_at from user_api_keys where user_id = %s", (uid,)
    ).fetchone()[0]
    assert after is not None
