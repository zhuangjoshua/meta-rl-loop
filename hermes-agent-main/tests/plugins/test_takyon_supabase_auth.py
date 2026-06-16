"""Supabase sub-user JWT verifier (AUTH0.md §7) — pure verifier tests, no live Supabase.

Mints tokens locally with a test secret and asserts the verifier is fail-closed: only a
well-signed, unexpired, correctly-audienced token with a subject is accepted.
"""

import time

import jwt
import pytest

from plugins.takyon import app_supabase_auth as sa

SECRET = "test-supabase-jwt-secret-0123456789"
SUB = "11111111-1111-1111-1111-111111111111"


def _token(secret=SECRET, **over):
    claims = {
        "sub": SUB,
        "email": "User@Example.com",
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
