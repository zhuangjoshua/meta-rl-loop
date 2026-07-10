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


@pytest.fixture(autouse=True)
def _stripe_requests_run_on_safebox(monkeypatch):
    # The raw Stripe REST helper is now a safebox-local authority primitive. Runtime planes call the
    # safebox action route instead of resolving STRIPE_SECRET_KEY themselves.
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    monkeypatch.delenv("TAKYON_STRIPE_MODE", raising=False)
    monkeypatch.delenv("TAKYON_STRIPE_ACCOUNT_ID", raising=False)
    with stripe_util._VERIFIED_LIVE_ACCOUNTS_LOCK:
        stripe_util._VERIFIED_LIVE_ACCOUNTS.clear()


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


@pytest.mark.parametrize("event", [{"livemode": False}, {}, {"livemode": "false"}])
def test_webhook_mode_gate_accepts_test_events_and_legacy_test_fixtures(
    monkeypatch, event
):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "test")
    assert stripe_util.validate_stripe_webhook_event_mode(event) is None


def test_webhook_mode_gate_rejects_live_event_in_test_mode(monkeypatch):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "test")
    with pytest.raises(StripeError, match="does not match"):
        stripe_util.validate_stripe_webhook_event_mode({"livemode": True})


def test_webhook_mode_gate_accepts_explicit_live_event_in_live_mode(monkeypatch):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    assert (
        stripe_util.validate_stripe_webhook_event_mode({"livemode": True}) is None
    )


@pytest.mark.parametrize(
    "event", [{"livemode": False}, {}, {"livemode": "true"}, {"livemode": 1}]
)
def test_webhook_mode_gate_rejects_mismatch_or_non_boolean_in_live_mode(
    monkeypatch, event
):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    with pytest.raises(StripeError, match="livemode"):
        stripe_util.validate_stripe_webhook_event_mode(event)


def test_request_without_key_raises(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setattr(stripe_util.safebox, "load_env", lambda: {})
    with pytest.raises(StripeError, match="STRIPE_SECRET_KEY"):
        stripe_request("checkout/sessions", {"mode": "payment"})


@pytest.mark.parametrize(
    ("key", "expected"),
    [("sk_live_x", True), ("rk_live_x", True), ("sk_test_x", False), ("rk_test_x", False)],
)
def test_stripe_key_livemode_is_derived_from_key_prefix(monkeypatch, key, expected):
    monkeypatch.setenv("STRIPE_SECRET_KEY", key)
    assert stripe_util.stripe_key_livemode() is expected


def test_stripe_key_livemode_rejects_unknown_key_shape(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "not-a-stripe-key")
    with pytest.raises(StripeError, match="unrecognized mode"):
        stripe_util.stripe_key_livemode()


def test_request_loads_key_from_safebox_env(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setattr(
        stripe_util.safebox,
        "load_env",
        lambda: {"STRIPE_SECRET_KEY": "sk_test_from_dotenv"},
    )
    captured: dict[str, str] = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"id":"cs_test_from_env"}'

    def _fake_urlopen(request, timeout=None):
        captured["auth"] = request.headers.get("Authorization")
        return _Resp()

    monkeypatch.setattr(stripe_util.urllib.request, "urlopen", _fake_urlopen)
    out = stripe_request("checkout/sessions", {"mode": "payment"})

    assert out["id"] == "cs_test_from_env"
    assert captured["auth"] == "Bearer sk_test_from_dotenv"


def test_request_sets_safebox_constructed_idempotency_header(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_secret")
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"id":"cs_test_idempotent"}'

    def _fake_urlopen(request, timeout=None):
        captured["idempotency"] = request.headers.get("Idempotency-key")
        return _Resp()

    monkeypatch.setattr(stripe_util.urllib.request, "urlopen", _fake_urlopen)
    result = stripe_request(
        "checkout/sessions",
        {"mode": "subscription"},
        idempotency_key="takyon-app-checkout-intent-123",
    )
    assert result["id"] == "cs_test_idempotent"
    assert captured["idempotency"] == "takyon-app-checkout-intent-123"


def test_branded_checkout_pins_minimum_stripe_api_version(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_secret")
    versions: list[str | None] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"id":"cs_test_branded"}'

    def _fake_urlopen(request, timeout=None):
        headers = {str(key).lower(): value for key, value in request.header_items()}
        versions.append(headers.get("stripe-version"))
        return _Resp()

    monkeypatch.setattr(stripe_util.urllib.request, "urlopen", _fake_urlopen)
    stripe_request(
        "checkout/sessions",
        {
            "mode": "subscription",
            "branding_settings[display_name]": "Climb Log",
        },
    )
    stripe_request("checkout/sessions", {"mode": "subscription"})

    assert versions == ["2025-09-30.clover", None]


@pytest.mark.parametrize("key", ["sk_test_secret", "rk_test_secret"])
def test_request_test_mode_accepts_test_secret_and_restricted_keys(monkeypatch, key):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", key)
    captured: dict[str, str] = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"id":"cs_test_mode"}'

    def _fake_urlopen(request, timeout=None):
        captured["auth"] = request.headers.get("Authorization")
        return _Resp()

    monkeypatch.setattr(stripe_util.urllib.request, "urlopen", _fake_urlopen)

    assert stripe_request("checkout/sessions", {})["id"] == "cs_test_mode"
    assert captured["auth"] == f"Bearer {key}"


@pytest.mark.parametrize("key", ["sk_live_secret", "rk_live_secret"])
def test_request_default_test_mode_rejects_live_keys_before_network(monkeypatch, key):
    monkeypatch.setenv("TAKYON_ENV", "prod")
    monkeypatch.setenv("STRIPE_SECRET_KEY", key)
    monkeypatch.setattr(
        stripe_util.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("mismatched key reached the network"),
    )

    with pytest.raises(StripeError, match="does not match TAKYON_STRIPE_MODE=test"):
        stripe_request("checkout/sessions", {})


@pytest.mark.parametrize("key", ["sk_live_secret", "rk_live_secret"])
def test_request_live_mode_accepts_live_keys_only_on_prod_safebox(monkeypatch, key):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_ENV", "prod")
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_STRIPE_ACCOUNT_ID", "acct_expected")
    monkeypatch.setenv("STRIPE_SECRET_KEY", key)
    captured: list[tuple[str, str, str]] = []

    class _Resp:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body

    def _fake_urlopen(request, timeout=None):
        captured.append(
            (request.method, request.full_url, request.headers.get("Authorization"))
        )
        if request.full_url == "https://api.stripe.com/v1/account":
            return _Resp(b'{"id":"acct_expected"}')
        return _Resp(b'{"id":"cs_live_mode"}')

    monkeypatch.setattr(stripe_util.urllib.request, "urlopen", _fake_urlopen)

    assert stripe_request("checkout/sessions", {})["id"] == "cs_live_mode"
    assert captured == [
        ("GET", "https://api.stripe.com/v1/account", f"Bearer {key}"),
        (
            "POST",
            "https://api.stripe.com/v1/checkout/sessions",
            f"Bearer {key}",
        ),
    ]


def test_request_live_mode_requires_expected_account_before_network(monkeypatch):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_ENV", "prod")
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_secret")
    monkeypatch.setattr(
        stripe_util.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("missing account guard reached the network"),
    )

    with pytest.raises(StripeError, match="requires TAKYON_STRIPE_ACCOUNT_ID"):
        stripe_request("checkout/sessions", {})


def test_request_live_account_mismatch_never_writes_and_is_not_cached(monkeypatch):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_ENV", "prod")
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_STRIPE_ACCOUNT_ID", "acct_expected")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_secret")
    captured: list[tuple[str, str]] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"id":"acct_wrong"}'

    def _fake_urlopen(request, timeout=None):
        captured.append((request.method, request.full_url))
        if request.method != "GET":
            pytest.fail("Stripe write occurred before account identity matched")
        return _Resp()

    monkeypatch.setattr(stripe_util.urllib.request, "urlopen", _fake_urlopen)

    for _ in range(2):
        with pytest.raises(StripeError, match="account mismatch"):
            stripe_request("checkout/sessions", {"mode": "payment"})

    assert captured == [
        ("GET", "https://api.stripe.com/v1/account"),
        ("GET", "https://api.stripe.com/v1/account"),
    ]


def test_request_live_account_success_is_cached_per_key_and_account(monkeypatch):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_ENV", "prod")
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_STRIPE_ACCOUNT_ID", "acct_expected")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_secret")
    captured: list[tuple[str, str]] = []

    class _Resp:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body

    def _fake_urlopen(request, timeout=None):
        captured.append((request.method, request.full_url))
        if request.full_url == "https://api.stripe.com/v1/account":
            return _Resp(b'{"id":"acct_expected"}')
        return _Resp(b'{"id":"cs_live_mode"}')

    monkeypatch.setattr(stripe_util.urllib.request, "urlopen", _fake_urlopen)

    stripe_request("checkout/sessions", {})
    stripe_request("checkout/sessions", {})
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_other_secret")
    stripe_request("checkout/sessions", {})

    assert captured == [
        ("GET", "https://api.stripe.com/v1/account"),
        ("POST", "https://api.stripe.com/v1/checkout/sessions"),
        ("POST", "https://api.stripe.com/v1/checkout/sessions"),
        ("GET", "https://api.stripe.com/v1/account"),
        ("POST", "https://api.stripe.com/v1/checkout/sessions"),
    ]


@pytest.mark.parametrize("key", ["sk_test_secret", "rk_test_secret"])
def test_request_live_mode_rejects_test_keys_before_network(monkeypatch, key):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_ENV", "prod")
    monkeypatch.setenv("STRIPE_SECRET_KEY", key)
    monkeypatch.setattr(
        stripe_util.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("mismatched key reached the network"),
    )

    with pytest.raises(StripeError, match="does not match TAKYON_STRIPE_MODE=live"):
        stripe_request("checkout/sessions", {})


@pytest.mark.parametrize(
    ("takyon_env", "host_role"),
    [("dev", "safebox"), ("prod", "operator"), ("", "safebox"), ("prod", "")],
)
def test_request_live_mode_rejects_non_prod_or_non_safebox_hosts_before_network(
    monkeypatch, takyon_env, host_role
):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_ENV", takyon_env)
    monkeypatch.setenv("TAKYON_HOST_ROLE", host_role)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_secret")
    monkeypatch.setattr(
        stripe_util.safebox,
        "read_env_backed_value",
        lambda _key: "sk_live_secret",
    )
    monkeypatch.setattr(
        stripe_util.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("live key reached the network"),
    )

    with pytest.raises(StripeError, match="requires TAKYON_ENV=prod"):
        stripe_request("checkout/sessions", {})


@pytest.mark.parametrize(
    "key", ["pk_test_publishable", "sk_future_secret", "secret", ""]
)
def test_request_rejects_unknown_key_prefixes_before_network(monkeypatch, key):
    monkeypatch.setenv("STRIPE_SECRET_KEY", key)
    monkeypatch.setattr(stripe_util.safebox, "load_env", lambda: {})
    monkeypatch.setattr(
        stripe_util.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("unknown key reached the network"),
    )

    expected = "STRIPE_SECRET_KEY" if not key else "unrecognized prefix"
    with pytest.raises(StripeError, match=expected):
        stripe_request("checkout/sessions", {})


def test_request_rejects_unknown_stripe_mode_before_network(monkeypatch):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "automatic")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_secret")
    monkeypatch.setattr(
        stripe_util.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("invalid mode reached the network"),
    )

    with pytest.raises(StripeError, match="TAKYON_STRIPE_MODE must be test or live"):
        stripe_request("checkout/sessions", {})


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
