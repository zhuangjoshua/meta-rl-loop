"""Phase 2 capability-token primitive (deploy/SAFEBOX-BROKER-REMEDIATION-PLAN.md).

These tests pin the property the whole tenant-isolation design rests on: a verified token's scope is
AUTHORITATIVE and IMMUTABLE — a client (which never holds the safebox signing key) cannot mint a token
for another user/business/sub-user, alter the scope of one it was given, or raise its own cost ceiling.
"""
import json

import pytest

from plugins.takyon.safebox_capability import (
    CapabilityError,
    CapabilityScope,
    _b64url,
    _b64url_decode,
    mint_capability,
    verify_capability,
)

KEY = b"safebox-only-signing-key-not-on-clients!"
AUD = "anthropic.messages"
NOW = 1_000_000


def _scope(**kw):
    base = dict(
        takyon_user_id="user_A",
        business_slug="climblog",
        app_user_id="cust_X",
        action="ai.generate",
        max_cost_microusd=2000,
    )
    base.update(kw)
    return CapabilityScope(**base)


def _mint(scope=None, **kw):
    opts = dict(signing_key=KEY, audience=AUD, nonce="n1", issued_at=NOW, ttl_seconds=300)
    opts.update(kw)
    return mint_capability(scope or _scope(), **opts)


def _tamper(token, field, value):
    body_b64, sig_b64 = token.split(".")
    body = json.loads(_b64url_decode(body_b64))
    body[field] = value
    new_body = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _b64url(new_body) + "." + sig_b64


def test_roundtrip_returns_authoritative_scope():
    scope, nonce, exp = verify_capability(_mint(), signing_key=KEY, expected_audience=AUD, now=NOW + 10)
    assert scope == _scope()
    assert nonce == "n1"
    assert exp == NOW + 300


@pytest.mark.parametrize(
    "field,value,label",
    [
        ("b", "jobtrackr", "cross-business swap"),
        ("tu", "user_B", "cross-user swap"),
        ("au", "cust_Y", "cross-sub-user swap"),
        ("mc", 100_000_000, "raise own cost ceiling"),
        ("act", "ad.video", "change action"),
    ],
)
def test_scope_is_immutable_after_signing(field, value, label):
    tampered = _tamper(_mint(), field, value)
    with pytest.raises(CapabilityError, match="bad signature"):
        verify_capability(tampered, signing_key=KEY, expected_audience=AUD, now=NOW + 10)


def test_wrong_signing_key_rejected():
    with pytest.raises(CapabilityError, match="bad signature"):
        verify_capability(_mint(), signing_key=b"a-client-guessed-key", expected_audience=AUD, now=NOW + 10)


def test_expired_rejected():
    with pytest.raises(CapabilityError, match="expired"):
        verify_capability(_mint(ttl_seconds=60), signing_key=KEY, expected_audience=AUD, now=NOW + 61)


def test_audience_mismatch_rejected():
    with pytest.raises(CapabilityError, match="audience"):
        verify_capability(_mint(), signing_key=KEY, expected_audience="gemini.image", now=NOW + 10)


def test_operator_plane_token_has_no_subuser():
    scope, _, _ = verify_capability(
        _mint(_scope(app_user_id=None)), signing_key=KEY, expected_audience=AUD, now=NOW + 10
    )
    assert scope.app_user_id is None
    assert scope.takyon_user_id == "user_A" and scope.business_slug == "climblog"


def test_incomplete_scope_refused_at_mint():
    with pytest.raises(CapabilityError):
        _mint(_scope(business_slug=""))
    with pytest.raises(CapabilityError):
        _mint(_scope(takyon_user_id=""))


def test_no_signing_key_cannot_mint_or_verify():
    with pytest.raises(CapabilityError):
        mint_capability(_scope(), signing_key=b"", audience=AUD, nonce="n", issued_at=NOW, ttl_seconds=300)
    with pytest.raises(CapabilityError):
        verify_capability(_mint(), signing_key=b"", expected_audience=AUD, now=NOW + 10)
