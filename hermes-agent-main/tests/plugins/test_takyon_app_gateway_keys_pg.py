"""Postgres integration tests for the project gateway-key boundary — Phase 5(e).

Phase 5 acceptance (this slice): "generated app never holds provider key". The boundary primitive
this pins:
  * a business is minted a ``tkg_…`` gateway key; only its SHA-256 hash + a non-secret prefix are
    stored (never the raw key), and presenting the raw key resolves to ONLY the business_slug +
    key_id — the opaque handle the internal AI gateway uses to front the shared provider key;
  * the gateway keyspace (``tkg_``) is DISJOINT from the per-user key keyspace (``tk_``): a user
    key never resolves as a gateway key and vice versa;
  * revocation is soft + idempotent + scopable to a business (one business can't revoke another's
    key);
  * a business may hold several active keys at once (the deliberate divergence from the
    one-active-per-user rule), including under concurrent mint;
  * deleting a business cascades its keys away, so a resolvable key always points at a live
    business.

Real engine on real Postgres (never mocks). Skips unless psycopg is importable and
TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_gateway_keys, user_api_keys  # noqa: E402
from plugins.takyon.app_gateway_keys import AppGatewayKeyError, GatewayPrincipal  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402
from plugins.takyon.user_api_keys import hash_api_key  # noqa: E402


def _sub() -> str:
    return f"auth0|{uuid.uuid4().hex}"


def _owner(conn) -> str:
    uid, _, _ = provision_user_on_first_login(conn, _sub())
    return uid


def _business(conn, owner_id=None, name="Acme") -> str:
    if owner_id is None:
        owner_id = _owner(conn)
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, name, owner_id),
    )
    return slug


def _new_conn(pg_conn):
    """A fresh autocommit connection to the SAME throwaway DB — for real concurrency."""
    return psycopg.connect(
        os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname, autocommit=True
    )


def _stored(conn, key_id):
    return conn.execute(
        "select key_hash, prefix from app_gateway_keys where id = %s", (key_id,)
    ).fetchone()


# --- mint + at-rest discipline ------------------------------------------------------------------


def test_mint_returns_tkg_key_and_stores_only_hash(pg_conn):
    slug = _business(pg_conn)
    raw, key = app_gateway_keys.mint_gateway_key(pg_conn, slug)

    assert raw.startswith("tkg_")
    assert key.business_slug == slug
    assert key.revoked_at is None
    # Stored: the SHA-256 hash + non-secret prefix, never the raw key.
    key_hash, prefix = _stored(pg_conn, key.id)
    assert key_hash == hash_api_key(raw)
    assert key_hash != raw
    assert len(key_hash) == 64  # sha256 hex
    assert prefix == app_gateway_keys.gateway_key_prefix(raw)
    assert raw.startswith(prefix)
    assert key.prefix == prefix


def test_resolve_returns_business_and_keyid_only(pg_conn):
    slug = _business(pg_conn)
    raw, key = app_gateway_keys.mint_gateway_key(pg_conn, slug)

    principal = app_gateway_keys.resolve_gateway_key(pg_conn, raw)
    assert isinstance(principal, GatewayPrincipal)
    assert principal.business_slug == slug
    assert principal.key_id == key.id
    # The boundary is exactly these two fields — nothing else leaks.
    assert set(vars(principal)) == {"business_slug", "key_id"}


# --- resolve rejects everything that isn't a live, well-formed, owned key -----------------------


@pytest.mark.parametrize("bad", ["", "garbage", "tkg_short", "tk_" + "a" * 40, 12345, None])
def test_resolve_malformed_returns_none(pg_conn, bad):
    assert app_gateway_keys.resolve_gateway_key(pg_conn, bad) is None


def test_resolve_unknown_wellformed_key_returns_none(pg_conn):
    # Structurally valid tkg_ key that was never minted.
    unknown = app_gateway_keys.generate_gateway_key()
    assert app_gateway_keys.is_well_formed(unknown)
    assert app_gateway_keys.resolve_gateway_key(pg_conn, unknown) is None


def test_resolve_revoked_returns_none(pg_conn):
    slug = _business(pg_conn)
    raw, _ = app_gateway_keys.mint_gateway_key(pg_conn, slug)
    assert app_gateway_keys.resolve_gateway_key(pg_conn, raw) is not None

    assert app_gateway_keys.revoke_gateway_key(pg_conn, raw_key=raw) is True
    assert app_gateway_keys.resolve_gateway_key(pg_conn, raw) is None


def test_user_key_and_gateway_key_keyspaces_are_disjoint(pg_conn):
    """A per-user tk_ key must never resolve as a gateway key, and vice versa — the disjoint-prefix
    boundary that keeps the two credential types from being confused."""
    slug = _business(pg_conn)
    gw_raw, _ = app_gateway_keys.mint_gateway_key(pg_conn, slug)
    user_raw = user_api_keys.generate_api_key()  # the tk_ keyspace

    # user key is not a well-formed gateway key, and doesn't resolve as one
    assert not app_gateway_keys.is_well_formed(user_raw)
    assert app_gateway_keys.resolve_gateway_key(pg_conn, user_raw) is None
    # gateway key is not a well-formed user key
    assert not user_api_keys.is_well_formed(gw_raw)


# --- ownership / FK guard -----------------------------------------------------------------------


def test_mint_unknown_business_raises_foreign_key(pg_conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        app_gateway_keys.mint_gateway_key(pg_conn, "no-such-business")


# --- multiple active keys per business (deliberate divergence from one-active-per-user) ---------


def test_business_may_hold_multiple_active_keys(pg_conn):
    slug = _business(pg_conn)
    raw1, k1 = app_gateway_keys.mint_gateway_key(pg_conn, slug)
    raw2, k2 = app_gateway_keys.mint_gateway_key(pg_conn, slug)

    assert k1.id != k2.id
    assert app_gateway_keys.resolve_gateway_key(pg_conn, raw1).business_slug == slug
    assert app_gateway_keys.resolve_gateway_key(pg_conn, raw2).business_slug == slug
    active = app_gateway_keys.list_gateway_keys(pg_conn, slug)
    assert {k.id for k in active} == {k1.id, k2.id}


# --- revoke: by raw / by id, idempotent, scoped, validated --------------------------------------


def test_revoke_by_raw_only_affects_that_key(pg_conn):
    slug = _business(pg_conn)
    raw1, k1 = app_gateway_keys.mint_gateway_key(pg_conn, slug)
    raw2, k2 = app_gateway_keys.mint_gateway_key(pg_conn, slug)

    assert app_gateway_keys.revoke_gateway_key(pg_conn, raw_key=raw1) is True
    assert app_gateway_keys.resolve_gateway_key(pg_conn, raw1) is None
    assert app_gateway_keys.resolve_gateway_key(pg_conn, raw2) is not None
    assert [k.id for k in app_gateway_keys.list_gateway_keys(pg_conn, slug)] == [k2.id]


def test_revoke_by_key_id(pg_conn):
    slug = _business(pg_conn)
    raw, key = app_gateway_keys.mint_gateway_key(pg_conn, slug)
    assert app_gateway_keys.revoke_gateway_key(pg_conn, key_id=key.id) is True
    assert app_gateway_keys.resolve_gateway_key(pg_conn, raw) is None


def test_revoke_is_idempotent(pg_conn):
    slug = _business(pg_conn)
    raw, _ = app_gateway_keys.mint_gateway_key(pg_conn, slug)
    assert app_gateway_keys.revoke_gateway_key(pg_conn, raw_key=raw) is True
    # second revoke moves nothing
    assert app_gateway_keys.revoke_gateway_key(pg_conn, raw_key=raw) is False
    assert app_gateway_keys.resolve_gateway_key(pg_conn, raw) is None


def test_revoke_unknown_key_returns_false(pg_conn):
    assert (
        app_gateway_keys.revoke_gateway_key(
            pg_conn, raw_key=app_gateway_keys.generate_gateway_key()
        )
        is False
    )


def test_revoke_scoped_to_business_cannot_cross_tenants(pg_conn):
    slug_a = _business(pg_conn, name="A")
    slug_b = _business(pg_conn, name="B")
    raw_a, _ = app_gateway_keys.mint_gateway_key(pg_conn, slug_a)

    # try to revoke A's key while scoping to B -> no-op, A's key still live
    assert (
        app_gateway_keys.revoke_gateway_key(pg_conn, raw_key=raw_a, business_slug=slug_b)
        is False
    )
    assert app_gateway_keys.resolve_gateway_key(pg_conn, raw_a) is not None
    # correctly scoped revoke works
    assert (
        app_gateway_keys.revoke_gateway_key(pg_conn, raw_key=raw_a, business_slug=slug_a)
        is True
    )
    assert app_gateway_keys.resolve_gateway_key(pg_conn, raw_a) is None


def test_revoke_requires_an_identifier(pg_conn):
    with pytest.raises(AppGatewayKeyError):
        app_gateway_keys.revoke_gateway_key(pg_conn)


# --- cross-business isolation + listing ---------------------------------------------------------


def test_resolve_cross_business_isolation(pg_conn):
    slug_a = _business(pg_conn, name="A")
    slug_b = _business(pg_conn, name="B")
    raw_a, _ = app_gateway_keys.mint_gateway_key(pg_conn, slug_a)
    raw_b, _ = app_gateway_keys.mint_gateway_key(pg_conn, slug_b)

    assert app_gateway_keys.resolve_gateway_key(pg_conn, raw_a).business_slug == slug_a
    assert app_gateway_keys.resolve_gateway_key(pg_conn, raw_b).business_slug == slug_b


def test_list_excludes_revoked_by_default(pg_conn):
    slug = _business(pg_conn)
    raw1, k1 = app_gateway_keys.mint_gateway_key(pg_conn, slug)
    _, k2 = app_gateway_keys.mint_gateway_key(pg_conn, slug)
    app_gateway_keys.revoke_gateway_key(pg_conn, raw_key=raw1)

    active = app_gateway_keys.list_gateway_keys(pg_conn, slug)
    assert [k.id for k in active] == [k2.id]
    all_keys = app_gateway_keys.list_gateway_keys(pg_conn, slug, include_revoked=True)
    assert {k.id for k in all_keys} == {k1.id, k2.id}


# --- business delete cascades keys --------------------------------------------------------------


def test_business_delete_cascades_keys(pg_conn):
    slug = _business(pg_conn)
    raw, key = app_gateway_keys.mint_gateway_key(pg_conn, slug)

    pg_conn.execute("delete from businesses where slug = %s", (slug,))

    assert app_gateway_keys.resolve_gateway_key(pg_conn, raw) is None
    remaining = pg_conn.execute(
        "select count(*) from app_gateway_keys where id = %s", (key.id,)
    ).fetchone()[0]
    assert remaining == 0


# --- concurrency: parallel mint never collides, all resolve -------------------------------------


def test_concurrent_mint_produces_unique_resolvable_keys(pg_conn):
    slug = _business(pg_conn)
    n = 8
    barrier = threading.Barrier(n)
    results: list[str] = []
    lock = threading.Lock()

    def worker():
        conn = _new_conn(pg_conn)
        try:
            barrier.wait()
            raw, _ = app_gateway_keys.mint_gateway_key(conn, slug)
            with lock:
                results.append(raw)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=n) as pool:
        for fut in [pool.submit(worker) for _ in range(n)]:
            fut.result()

    assert len(results) == n
    assert len(set(results)) == n  # no two mints collide
    for raw in results:
        assert app_gateway_keys.resolve_gateway_key(pg_conn, raw).business_slug == slug
    assert len(app_gateway_keys.list_gateway_keys(pg_conn, slug)) == n
