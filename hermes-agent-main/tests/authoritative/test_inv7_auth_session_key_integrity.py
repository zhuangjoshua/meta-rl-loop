"""Authoritative red-team suite — INVARIANT 7.

GOAL_RULES.md §3 invariant 7:

    "Auth/session/key integrity fail-closed — hashed key verifiers (single active
    key/user), JWTs require exp+sub+audience, sessions/magic-links hashed +
    single-use."

This module asserts that invariant against the REAL Takyon source — assume every
caller (operator AND sub-user) is EVIL and trying to forge an identity, replay a
one-time secret, or hold two live credentials at once.

Three credential rails are covered:

  1. Top-level user API keys
     - `plugins.takyon.user_api_keys` — opaque `tk_` keys are SHA-256 hashed at
       rest (`hash_api_key`), verified in constant time (`verify_api_key` /
       `hmac.compare_digest`); the raw key is never stored.
     - `plugins.takyon.safebox.register_user_api_key` — the verifier registry
       refuses a *second active* key for one user (single active key/user).
     - `plugins.takyon.control_plane.resolve_api_key` — resolves a presented raw
       key through the Safebox hashed registry, fail-closed for malformed /
       unknown / revoked keys.
     - DB layer (PG-gated): `user_api_keys` has a partial-unique constraint so a
       second active row is a `UniqueViolation`.

  2. Sub-user Supabase JWTs
     - `plugins.takyon.app_supabase_auth.verify_supabase_jwt` — fail-closed:
       requires `exp` + `sub`, audience must match (`"authenticated"`), bad
       signature / expiry / wrong-aud / missing token are all refused. Exercised
       on the ES256 / JWKS path with a self-generated keypair (mirrors
       tests/plugins/test_takyon_supabase_auth.py), so no live Supabase or secret
       is needed.

  3. Sub-user magic links + sessions
     - `plugins.takyon.app_identity._hash_token` — SHA-256 of the raw token is the
       only at-rest form (links AND sessions).
     - `plugins.takyon.app_identity.verify_magic_link` — single-use redemption via
       `update ... where used_at is null ... returning`; a reused link is refused
       (PG-gated).
     - `plugins.takyon.app_identity.validate_session` — sessions resolve only by
       their hash, while unrevoked + unexpired + active.

Hermeticity: the credit-scrubbing + TAKYON_HOME-redirecting autouse fixtures in
tests/conftest.py cover the source/unit assertions with NO credential or network.
The handful of DB-truth checks use the repo's `pg_conn` fixture and are skipped
unless TAKYON_TEST_PG_DSN is set.

GREEN here = the invariant holds (forged/expired/wrong-aud/reused/duplicate ⇒
refused). RED would mean a money/identity hole opened (a forged or replayed
credential was accepted, or two active keys coexist) — a regression that VOIDs
the cycle. This invariant is NOT aspirational: current code is expected GREEN.
"""

from __future__ import annotations

import hmac
import inspect
import time
import uuid

import pytest

from plugins.takyon import app_identity
from plugins.takyon import app_supabase_auth as sa
from plugins.takyon import control_plane
from plugins.takyon import safebox
from plugins.takyon import user_api_keys

jwt = pytest.importorskip("jwt")  # PyJWT — present in the repo venv
ec = pytest.importorskip(
    "cryptography.hazmat.primitives.asymmetric.ec",
    reason="cryptography needed for the ES256/JWKS fail-closed checks",
)


# --------------------------------------------------------------------------- helpers


_SUB = "11111111-1111-1111-1111-111111111111"
_HS_SECRET = "test-supabase-jwt-secret-0123456789"


def _hs_token(secret: str = _HS_SECRET, **over) -> str:
    """A Supabase-shaped HS256 access token (well-formed unless `over` breaks it)."""
    claims = {
        "sub": _SUB,
        "email": "User@Example.com",
        "aud": "authenticated",
        "exp": int(time.time()) + 600,
    }
    claims.update(over)
    # Allow callers to drop a claim entirely by passing it as the sentinel below.
    claims = {k: v for k, v in claims.items() if v is not _DROP}
    return jwt.encode(claims, secret, algorithm="HS256")


class _Drop:  # pragma: no cover - sentinel
    pass


_DROP = _Drop()


def _es256_keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


def _es256_token(private_key, **over) -> str:
    claims = {
        "sub": _SUB,
        "email": "ec@example.com",
        "aud": "authenticated",
        "exp": int(time.time()) + 600,
    }
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not _DROP}
    return jwt.encode(
        claims, private_key, algorithm="ES256", headers={"kid": "kid-1"}
    )


def _install_jwks(monkeypatch, public_key):
    """Point the verifier's JWKS client at a locally-generated public key."""
    from types import SimpleNamespace

    class _FakeJwks:
        def get_signing_key_from_jwt(self, _token):
            return SimpleNamespace(key=public_key)

    monkeypatch.setattr(sa, "_jwks_client", lambda _url: _FakeJwks())


# =========================================================================== #
# Rail 1 — top-level user API keys: hashed at rest + constant-time verify
# =========================================================================== #


def test_api_key_stored_only_as_sha256_hash_never_plaintext():
    """The at-rest form is SHA-256 hex of the raw key — never the raw key itself."""
    import hashlib

    raw = user_api_keys.generate_api_key()
    stored = user_api_keys.hash_api_key(raw)

    assert stored == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert stored != raw
    assert raw not in stored
    # 64 hex chars => SHA-256, irreversible.
    assert len(stored) == 64 and all(c in "0123456789abcdef" for c in stored)


def test_api_key_verify_is_constant_time_and_rejects_forgery():
    raw = user_api_keys.generate_api_key()
    stored = user_api_keys.hash_api_key(raw)

    assert user_api_keys.verify_api_key(raw, stored) is True
    # A forged / wrong key with a valid-looking shape must not verify.
    forged = user_api_keys.generate_api_key()
    assert user_api_keys.verify_api_key(forged, stored) is False
    # Empty inputs fail closed (no compare against empty).
    assert user_api_keys.verify_api_key("", stored) is False
    assert user_api_keys.verify_api_key(raw, "") is False


def test_api_key_verify_uses_hmac_compare_digest_source_guard():
    """Structural guard: the verifier must compare via hmac.compare_digest, not `==`,
    so a timing oracle cannot leak the stored hash byte-by-byte."""
    src = inspect.getsource(user_api_keys.verify_api_key)
    assert "compare_digest" in src
    assert "hmac" in inspect.getsource(user_api_keys)
    # And the primitive is actually the constant-time one.
    assert user_api_keys.hmac.compare_digest is hmac.compare_digest


def test_api_key_well_formed_gate_rejects_garbage_before_any_lookup():
    assert user_api_keys.is_well_formed(user_api_keys.generate_api_key()) is True
    assert user_api_keys.is_well_formed("") is False
    assert user_api_keys.is_well_formed("not-a-tk-key") is False
    # right prefix, too-short body
    assert user_api_keys.is_well_formed("tk_short") is False
    # right prefix, illegal char in body
    assert user_api_keys.is_well_formed("tk_" + "!" * 40) is False


def test_safebox_registry_blocks_second_active_key_per_user(monkeypatch):
    """Single active key/user, enforced at the Safebox verifier registry.

    Local registry path (no remote authority): registering a second *active* key
    for the same user raises ValueError. Runs hermetically — TAKYON_HOME is the
    conftest temp dir; we only flip the host role so the local file registry is
    used instead of a remote service.
    """
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    user_id = f"user-{uuid.uuid4().hex[:8]}"

    first = user_api_keys.generate_api_key()
    safebox.register_user_api_key(user_id, first, key_id=str(uuid.uuid4()))

    with pytest.raises(ValueError, match="active user api key already exists"):
        safebox.register_user_api_key(
            user_id, user_api_keys.generate_api_key(), key_id=str(uuid.uuid4())
        )

    # The first key still resolves; the registry stores a hash, not the raw key.
    resolved = safebox.resolve_user_api_key(first)
    assert resolved is not None
    assert resolved["key_hash"] != first
    assert resolved["key_hash"] == user_api_keys.hash_api_key(first)


def test_safebox_resolve_fails_closed_on_revoked_and_garbage(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    user_id = f"user-{uuid.uuid4().hex[:8]}"

    raw = user_api_keys.generate_api_key()
    record = safebox.register_user_api_key(user_id, raw, key_id=str(uuid.uuid4()))
    assert safebox.resolve_user_api_key(raw) is not None

    # Malformed presented key never resolves.
    assert safebox.resolve_user_api_key("tk_bogus") is None
    assert safebox.resolve_user_api_key("") is None

    # After revocation, the (still-valid-shape) raw key is dead.
    assert safebox.revoke_user_api_key(str(record["id"])) is True
    assert safebox.resolve_user_api_key(raw) is None


def test_control_plane_resolve_api_key_uses_hashed_registry_and_is_fail_closed(
    monkeypatch,
):
    """`control_plane.resolve_api_key` must go through the Safebox hashed registry
    (never compare a raw key) and refuse malformed/unknown/revoked/non-active."""
    src = inspect.getsource(control_plane.resolve_api_key)
    # It delegates verification to the hashed Safebox registry.
    assert "safebox.resolve_user_api_key" in src
    # It refuses non-active users (fail-closed on status).
    assert 'status != "active"' in src or "status != 'active'" in src
    # It returns None (refusal) on the unknown-record path.
    assert "return None" in src

    # Behavior: with the local Safebox authority enabled, an unknown raw key
    # resolves to None without any DB connection, because the hashed registry has
    # no matching hash. We pass a dummy conn whose .execute would raise — proving
    # resolution short-circuits at the hashed-registry miss, never reaching a
    # trust-the-caller fallback.
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)

    class _ExplodingConn:
        def execute(self, *a, **k):  # pragma: no cover - must never be hit
            raise AssertionError("resolve_api_key must not query the DB on a miss")

    assert control_plane.resolve_api_key(_ExplodingConn(), "tk_unknown_key") is None
    assert control_plane.resolve_api_key(_ExplodingConn(), "") is None


def test_mint_then_rotate_keeps_single_active_key_invariant_in_source():
    """`mint_api_key` raises if one is already active; `rotate_api_key` revokes the
    old before minting — so the user is never holding two live keys."""
    mint_src = inspect.getsource(control_plane.mint_api_key)
    rotate_src = inspect.getsource(control_plane.rotate_api_key)
    # mint goes through the same record minter that registers in Safebox.
    assert "_mint_api_key_record" in mint_src
    # rotate revokes the prior active key (DB + Safebox) before minting a new one.
    assert "revoked_at = now()" in rotate_src
    assert "revoke_user_api_keys_for_user" in rotate_src


# =========================================================================== #
# Rail 2 — Supabase JWT verifier: require exp+sub+audience, fail-closed
# =========================================================================== #


def test_jwt_verifier_requires_exp_and_sub_in_decode_options():
    """Both decode paths must pass options={"require": ["exp", "sub"]} so a token
    missing either claim is rejected by PyJWT itself, not trusted."""
    for fn in (sa._decode_hs_token, sa._decode_asymmetric_token):
        src = inspect.getsource(fn)
        assert '"require"' in src or "'require'" in src
        assert '"exp"' in src and '"sub"' in src
    # The public verifier defaults the audience to Supabase's "authenticated".
    assert sa._DEFAULT_AUDIENCE == "authenticated"
    sig = inspect.signature(sa.verify_supabase_jwt)
    assert sig.parameters["audience"].default == "authenticated"


def test_jwt_es256_valid_token_accepted_via_jwks(monkeypatch):
    private_key, public_key = _es256_keypair()
    _install_jwks(monkeypatch, public_key)
    token = _es256_token(private_key)

    ident = sa.verify_supabase_jwt(token, project_url="https://example.supabase.co")
    assert ident.supabase_user_id == _SUB
    assert ident.email == "ec@example.com"


def test_jwt_forged_signature_refused(monkeypatch):
    """A token signed by an attacker key is refused when verified against the real
    JWKS public key (different keypair => bad signature)."""
    attacker_key, _ = _es256_keypair()
    _, real_public = _es256_keypair()
    _install_jwks(monkeypatch, real_public)
    forged = _es256_token(attacker_key)

    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt(forged, project_url="https://example.supabase.co")


def test_jwt_expired_token_refused(monkeypatch):
    private_key, public_key = _es256_keypair()
    _install_jwks(monkeypatch, public_key)
    expired = _es256_token(private_key, exp=int(time.time()) - 30)

    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt(expired, project_url="https://example.supabase.co")


def test_jwt_wrong_audience_refused(monkeypatch):
    private_key, public_key = _es256_keypair()
    _install_jwks(monkeypatch, public_key)
    wrong_aud = _es256_token(private_key, aud="not-authenticated")

    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt(wrong_aud, project_url="https://example.supabase.co")


def test_jwt_missing_exp_refused(monkeypatch):
    private_key, public_key = _es256_keypair()
    _install_jwks(monkeypatch, public_key)
    no_exp = _es256_token(private_key, exp=_DROP)

    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt(no_exp, project_url="https://example.supabase.co")


def test_jwt_missing_sub_refused(monkeypatch):
    private_key, public_key = _es256_keypair()
    _install_jwks(monkeypatch, public_key)
    no_sub = _es256_token(private_key, sub=_DROP)

    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt(no_sub, project_url="https://example.supabase.co")


def test_jwt_empty_token_refused():
    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt("", secret=_HS_SECRET)


def test_jwt_hs256_bad_secret_refused():
    """HS path is also fail-closed on a wrong shared secret."""
    token = _hs_token()
    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt(token, secret="the-wrong-secret")


# =========================================================================== #
# Rail 3 — magic links + sessions: hashed at rest, single-use
# =========================================================================== #


def test_token_hash_is_sha256_only_form_for_links_and_sessions():
    import hashlib

    raw = "some-raw-opaque-token-value"
    h = app_identity._hash_token(raw)
    assert h == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert h != raw and len(h) == 64


def test_magic_link_is_single_use_in_source():
    """The redemption is an atomic `update ... where used_at is null ... returning`
    so a replayed link can never win a second time."""
    src = inspect.getsource(app_identity.verify_magic_link)
    assert "update app_magic_links set used_at = now()" in src
    assert "used_at is null" in src
    assert "expires_at > now()" in src
    # Only the hash of the presented token is matched — the raw token is never
    # compared or stored.
    assert "_hash_token(token)" in src
    assert "InvalidMagicLink" in src


def test_magic_link_mint_stores_hash_not_raw_in_source():
    src = inspect.getsource(app_identity.create_magic_link)
    assert "_hash_token(raw)" in src
    # The raw token is returned to the caller exactly once, not persisted in clear.
    assert "return link, raw" in src


def test_session_validation_keys_off_hash_and_is_fail_closed_in_source():
    src = inspect.getsource(app_identity.validate_session)
    assert "_hash_token(token)" in src
    assert "s.revoked_at is null" in src
    assert "s.expires_at > now()" in src
    assert "u.status = 'active'" in src
    # Garbage / empty token => None (fail-closed), no DB hit.
    assert app_identity.validate_session(_NeverConn(), "biz", "") is None
    assert app_identity.validate_session(_NeverConn(), "biz", "   ") is None


class _NeverConn:
    def execute(self, *a, **k):  # pragma: no cover - must never run for empty tokens
        raise AssertionError("validate_session must short-circuit empty tokens")


# =========================================================================== #
# PG-gated DB-truth checks (skipped unless TAKYON_TEST_PG_DSN is set)
# =========================================================================== #


def test_pg_user_api_keys_partial_unique_blocks_second_active_row(pg_conn):
    """DB layer mirrors the Safebox single-active-key rule: a second un-revoked row
    for one user violates the partial-unique index. Needs the Postgres rig."""
    pg_errors = pytest.importorskip("psycopg.errors")

    uid = pg_conn.execute(
        "insert into users (auth0_sub, email) values (%s, %s) returning id",
        (f"auth0|{uuid.uuid4().hex}", None),
    ).fetchone()[0]

    def _add(prefix: str):
        return pg_conn.execute(
            "insert into user_api_keys (user_id, key_hash, prefix) "
            "values (%s, %s, %s) returning id",
            (uid, uuid.uuid4().hex, prefix),
        ).fetchone()[0]

    _add("tk_aaaa1111")
    with pytest.raises(pg_errors.UniqueViolation):
        _add("tk_bbbb2222")


def test_pg_magic_link_single_use_reused_link_refused(pg_conn):
    """End-to-end single-use: first verify succeeds, a replay of the same raw token
    is refused with InvalidMagicLink. Needs the Postgres rig."""
    # Owner + business so the FK from app_users -> businesses(slug) holds.
    uid = pg_conn.execute(
        "insert into users (auth0_sub, email) values (%s, %s) returning id",
        (f"auth0|{uuid.uuid4().hex}", None),
    ).fetchone()[0]
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    pg_conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", uid),
    )

    _link, raw = app_identity.create_magic_link(pg_conn, slug, "user@example.com")

    session, raw_session = app_identity.verify_magic_link(pg_conn, slug, raw)
    assert session.business_slug == slug

    # Replay the very same magic-link token => refused.
    with pytest.raises(app_identity.InvalidMagicLink):
        app_identity.verify_magic_link(pg_conn, slug, raw)

    # The freshly-minted session is hashed at rest, never stored raw.
    stored_hash = pg_conn.execute(
        "select token_hash from app_sessions where id = %s", (session.id,)
    ).fetchone()[0]
    assert stored_hash == app_identity._hash_token(raw_session)
    assert stored_hash != raw_session

    # And it validates only via its hash, returning the owning sub-user.
    who = app_identity.validate_session(pg_conn, slug, raw_session)
    assert who is not None
    assert who.id == session.app_user_id
    # A garbage session token is refused.
    assert app_identity.validate_session(pg_conn, slug, "tk_not_a_session") is None
