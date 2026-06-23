"""Supabase sub-user JWT verifier (AUTH0.md §7) — local verifier tests, no live Supabase.

Mints tokens locally with a test secret and asserts the verifier is fail-closed: only a
well-signed, unexpired, correctly-audienced token with a subject is accepted.
"""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from plugins.takyon import app_supabase_auth as sa
from plugins.takyon import safebox

SECRET = "test-supabase-jwt-secret-0123456789"
SUB = "11111111-1111-1111-1111-111111111111"


def _token(secret=SECRET, **over):
    claims = {
        "sub": SUB,
        "email": "User@Example.com",
        "email_verified": True,
        "aud": "authenticated",
        "exp": int(time.time()) + 600,
        **over,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def test_verifies_valid_token_and_normalizes_email():
    ident = sa.verify_supabase_jwt(_token(), secret=SECRET)
    assert ident.supabase_user_id == SUB
    assert ident.email == "user@example.com"  # lower-cased


def test_rejects_bad_signature():
    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt(_token(), secret="the-wrong-secret")


def test_rejects_expired():
    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt(_token(exp=int(time.time()) - 30), secret=SECRET)


def test_rejects_wrong_audience():
    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt(_token(aud="not-authenticated"), secret=SECRET)


def test_rejects_unverified_email_without_auth_server_confirmation(monkeypatch):
    monkeypatch.setattr(sa, "_publishable_key", lambda: "")
    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt(_token(email_verified=False), secret=SECRET)


def test_rejects_missing_secret():
    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt(_token(), secret="")


def test_rejects_empty_token():
    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt("", secret=SECRET)


def test_rejects_token_without_subject():
    bad = jwt.encode(
        {"aud": "authenticated", "exp": int(time.time()) + 600, "email": "a@b.co"},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt(bad, secret=SECRET)


def test_verifies_es256_token_via_jwks(monkeypatch):
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    token = jwt.encode(
        {
            "sub": SUB,
            "email": "ec@example.com",
            "email_verified": True,
            "aud": "authenticated",
            "exp": int(time.time()) + 600,
        },
        private_key,
        algorithm="ES256",
        headers={"kid": "kid-1"},
    )

    class FakeJwksClient:
        def get_signing_key_from_jwt(self, _token):
            return SimpleNamespace(key=public_key)

    monkeypatch.setattr(sa, "_jwks_client", lambda _url: FakeJwksClient())

    ident = sa.verify_supabase_jwt(token, project_url="https://example.supabase.co")
    assert ident.supabase_user_id == SUB
    assert ident.email == "ec@example.com"


def test_verifies_hs256_token_via_auth_server_when_secret_missing(monkeypatch):
    token = _token()
    monkeypatch.setattr(sa, "_jwt_secret", lambda: "")
    monkeypatch.setattr(sa, "_publishable_key", lambda: "sb_publishable_test")
    monkeypatch.setattr(
        sa,
        "_verified_user_via_auth_server",
        lambda _token, *, project_url, publishable_key: {
            "id": SUB,
            "email": "user@example.com",
            "email_confirmed_at": "2026-06-21T00:00:00Z",
            "project_url": project_url,
            "publishable_key": publishable_key,
        },
    )

    ident = sa.verify_supabase_jwt(token, project_url="https://example.supabase.co")
    assert ident.supabase_user_id == SUB
    assert ident.email == "user@example.com"


def test_ambient_jwt_secret_not_used_for_hs_verification(monkeypatch):
    # Alg-confusion fix: an HS token must NOT be verified with the ambient SUPABASE_JWT_SECRET even when
    # one is configured. A caller who obtained the secret must not be able to forge product JWTs — so
    # with the ambient secret set but the server-side auth path failing, verification FAILS (it does not
    # fall back to local symmetric verify).
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setattr(sa, "_publishable_key", lambda: "sb_publishable_test")

    def _boom(*a, **k):
        raise sa.SupabaseAuthError("auth server rejected")

    monkeypatch.setattr(sa, "_verified_user_via_auth_server", _boom)
    with pytest.raises(sa.SupabaseAuthError):
        sa.verify_supabase_jwt(_token(), project_url="https://example.supabase.co")


def test_resolves_public_supabase_url_via_safebox_alias_lookup(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setattr(
        safebox,
        "first_env_backed_value",
        lambda *keys: "https://example.supabase.co" if "SUPABASE_URL" in keys else "",
    )
    monkeypatch.setattr(
        safebox,
        "read_env_backed_value",
        lambda _key: (_ for _ in ()).throw(KeyError("non-sensitive")),
    )

    assert sa._project_url() == "https://example.supabase.co"
