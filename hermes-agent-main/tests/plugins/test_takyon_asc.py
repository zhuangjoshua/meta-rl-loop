"""ASC leaf (plugins/takyon/asc.py) — pure tests, no network, no real key.

Pins: the ES256 JWT shape Apple requires (kid header, iss/aud/exp claims, ≤20-min TTL clamp);
the health classification table incl. the exact agreement-block error code; the never-raise
receipt contract on transport failure (pulse callers must stay best-effort)."""

from __future__ import annotations

import pytest

jwt = pytest.importorskip("jwt")
httpx = pytest.importorskip("httpx")

from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

from plugins.takyon import asc  # noqa: E402


@pytest.fixture(scope="module")
def key_pair():
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key()
    return pem, pub


def test_mint_jwt_shape_and_claims(key_pair):
    pem, pub = key_pair
    token = asc.mint_asc_jwt("KEYID1", "issuer-uuid", pem, ttl_seconds=600)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == "KEYID1"
    claims = jwt.decode(token, pub, algorithms=["ES256"], audience=asc.ASC_AUDIENCE)
    assert claims["iss"] == "issuer-uuid"
    assert claims["exp"] - claims["iat"] == 600


def test_mint_jwt_ttl_clamp_and_required_args(key_pair):
    pem, _ = key_pair
    with pytest.raises(asc.AscError):
        asc.mint_asc_jwt("K", "I", pem, ttl_seconds=asc.MAX_JWT_TTL_SECONDS + 1)
    with pytest.raises(asc.AscError):
        asc.mint_asc_jwt("", "I", pem)
    with pytest.raises(asc.AscError):
        asc.mint_asc_jwt("K", "I", "")


def _probe_with(status_code, body, key_pair):
    pem, _ = key_pair
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status_code, json=body)
    )
    return asc.probe_account_health("K", "I", pem, transport=transport)


def test_probe_ok(key_pair):
    r = _probe_with(200, {"data": []}, key_pair)
    assert r["state"] == asc.HEALTH_OK
    assert r["status_code"] == 200


def test_probe_agreement_blocked_exact_code(key_pair):
    body = {"errors": [{"code": asc.AGREEMENT_ERROR_CODE, "detail": "sign the agreement"}]}
    r = _probe_with(403, body, key_pair)
    assert r["state"] == asc.HEALTH_AGREEMENT_BLOCKED
    assert asc.AGREEMENT_ERROR_CODE in r["detail"]


def test_probe_other_403_is_error_not_agreement(key_pair):
    body = {"errors": [{"code": "FORBIDDEN.SOMETHING_ELSE", "detail": "nope"}]}
    r = _probe_with(403, body, key_pair)
    assert r["state"] == asc.HEALTH_ERROR


def test_probe_401_is_auth_error(key_pair):
    r = _probe_with(401, {"errors": [{"code": "NOT_AUTHORIZED"}]}, key_pair)
    assert r["state"] == asc.HEALTH_AUTH_ERROR


def test_probe_transport_failure_returns_unreachable_never_raises(key_pair):
    pem, _ = key_pair

    def _boom(request):
        raise httpx.ConnectError("nope")

    r = asc.probe_account_health("K", "I", pem, transport=httpx.MockTransport(_boom))
    assert r["state"] == asc.HEALTH_UNREACHABLE


def test_probe_bad_pem_returns_error_never_raises():
    r = asc.probe_account_health("K", "I", "not a pem")
    assert r["state"] == asc.HEALTH_ERROR
    assert "jwt_mint_failed" in r["detail"]
