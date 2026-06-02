"""Hermetic unit tests for the self-contained control-plane Stripe helpers
(plugins/takyon/stripe_util.py). Pure stdlib: no network (the REST success/error paths
mock urllib), no psycopg, no live keys (signature tests sign locally with
build_signature_header so they round-trip without Stripe). Runs everywhere — these are
NOT gated on TAKYON_TEST_PG_DSN."""

from __future__ import annotations

import io
import time
import urllib.error

import pytest

from plugins.takyon import stripe_util
from plugins.takyon.stripe_util import (
    StripeError,
    build_signature_header,
    stripe_request,
    verify_stripe_signature,
)

_SECRET = "whsec_test_abc123"
_BODY = '{"id":"evt_1","type":"checkout.session.completed"}'


def test_verify_roundtrips_freshly_signed_payload():
    header = build_signature_header(_BODY, _SECRET)
    # Returns None (raises nothing) on a good signature.
    assert verify_stripe_signature(_BODY, header, _SECRET) is None


def test_verify_rejects_tampered_body():
    header = build_signature_header(_BODY, _SECRET)
    with pytest.raises(StripeError, match="verification failed"):
        verify_stripe_signature(_BODY + " ", header, _SECRET)


def test_verify_rejects_wrong_secret():
    header = build_signature_header(_BODY, _SECRET)
    with pytest.raises(StripeError, match="verification failed"):
        verify_stripe_signature(_BODY, header, "whsec_the_wrong_secret")


def test_verify_rejects_stale_timestamp():
    stale = int(time.time()) - 400  # outside the 300s replay window
    header = build_signature_header(_BODY, _SECRET, timestamp=stale)
    with pytest.raises(StripeError, match="outside tolerance"):
        verify_stripe_signature(_BODY, header, _SECRET)


@pytest.mark.parametrize("bad", ["", "v1=deadbeef", "t=123", "garbage", "t=,v1="])
def test_verify_rejects_malformed_header(bad):
    with pytest.raises(StripeError):
        verify_stripe_signature(_BODY, bad, _SECRET)


def test_verify_rejects_non_integer_timestamp():
    with pytest.raises(StripeError, match="timestamp"):
        verify_stripe_signature(_BODY, "t=notanumber,v1=deadbeef", _SECRET)


def test_request_without_key_raises(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    with pytest.raises(StripeError, match="STRIPE_SECRET_KEY"):
        stripe_request("checkout/sessions", {"mode": "payment"})


def test_request_success_drops_none_and_targets_v1(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xyz")
    captured: dict[str, str] = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"id":"cs_test_1","url":"https://checkout.stripe.com/c/cs_test_1"}'

    def _fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = request.data.decode("utf-8")
        return _Resp()

    monkeypatch.setattr(stripe_util.urllib.request, "urlopen", _fake_urlopen)
    out = stripe_request("checkout/sessions", {"mode": "payment", "skip": None, "n": 3})

    assert out["id"] == "cs_test_1"
    assert captured["url"] == "https://api.stripe.com/v1/checkout/sessions"
    assert captured["method"] == "POST"
    assert captured["auth"] == "Bearer sk_test_xyz"
    # None-valued params are dropped; present ones are form-encoded.
    assert "skip" not in captured["body"]
    assert "mode=payment" in captured["body"]
    assert "n=3" in captured["body"]


def test_request_http_error_becomes_stripe_error(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xyz")

    def _raise(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url,
            402,
            "Payment Required",
            {},
            io.BytesIO(b'{"error":"card_declined"}'),
        )

    monkeypatch.setattr(stripe_util.urllib.request, "urlopen", _raise)
    with pytest.raises(StripeError, match="402") as excinfo:
        stripe_request("checkout/sessions", {"mode": "payment"})
    # The upstream error body is preserved in the message, never swallowed.
    assert "card_declined" in str(excinfo.value)


def test_request_get_uses_querystring_and_no_body(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xyz")
    captured: dict[str, object] = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"id":"acct_test_1","payouts_enabled":true}'

    def _fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["body"] = request.data
        return _Resp()

    monkeypatch.setattr(stripe_util.urllib.request, "urlopen", _fake_urlopen)
    out = stripe_request(
        "accounts/acct_test_1",
        {"expand[]": "capabilities"},
        method="GET",
    )

    assert out["id"] == "acct_test_1"
    assert captured["method"] == "GET"
    assert captured["body"] is None
    assert (
        captured["url"]
        == "https://api.stripe.com/v1/accounts/acct_test_1?expand%5B%5D=capabilities"
    )
