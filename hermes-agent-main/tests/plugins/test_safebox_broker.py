"""Phase 1/2 broker core (deploy/SAFEBOX-BROKER-REMEDIATION-PLAN.md). Pins the full broker property:
verify -> single-use -> reserve-before-call (on the validated scope) -> key used ONLY inside -> key-free
result -> release on failure -> ceiling enforced."""
import pytest

from plugins.takyon.safebox_broker import BrokerError, broker_call, handle_provider_request
from plugins.takyon.safebox_capability import CapabilityError, CapabilityScope, mint_capability
from plugins.takyon.safebox_nonce import InMemoryNonceStore

KEY = b"safebox-only-signing-key-not-on-clients!"
AUD = "anthropic.messages"
NOW = 1_000_000


def _scope(**kw):
    base = dict(
        takyon_user_id="user_A", business_slug="climblog", app_user_id="cust_X",
        action="ai.generate", max_cost_microusd=5000,
    )
    base.update(kw)
    return CapabilityScope(**base)


def _mint(scope=None, nonce="n1", ttl=300, aud=AUD):
    return mint_capability(scope or _scope(), signing_key=KEY, audience=aud, nonce=nonce, issued_at=NOW, ttl_seconds=ttl)


class FakeLedger:
    def __init__(self):
        self.events = []

    def reserve(self, scope, est):
        self.events.append(("reserve", scope.business_slug, scope.app_user_id, est))
        return {"scope": scope}

    def settle(self, res, actual):
        self.events.append(("settle", actual))

    def release(self, res):
        self.events.append(("release",))


def test_broker_call_delegates_with_authoritative_scope():
    seen = {}

    def _exec(s):
        seen["s"] = s
        return "ok"

    out = broker_call(
        token=_mint(), signing_key=KEY, expected_audience=AUD, now=NOW + 10,
        nonce_store=InMemoryNonceStore(), execute=_exec,
    )
    assert out == "ok"
    assert seen["s"].business_slug == "climblog" and seen["s"].app_user_id == "cust_X"


def test_replay_blocked():
    store = InMemoryNonceStore()
    tok = _mint()
    broker_call(token=tok, signing_key=KEY, expected_audience=AUD, now=NOW + 10, nonce_store=store, execute=lambda s: "ok")
    with pytest.raises(BrokerError, match="replayed_token"):
        broker_call(token=tok, signing_key=KEY, expected_audience=AUD, now=NOW + 11, nonce_store=store, execute=lambda s: "ok")


def test_bad_token_rejected_before_execute():
    called = []
    with pytest.raises(CapabilityError):
        broker_call(
            token="garbage.tok", signing_key=KEY, expected_audience=AUD, now=NOW + 10,
            nonce_store=InMemoryNonceStore(), execute=lambda s: called.append(1),
        )
    assert not called


def test_provider_request_reserves_calls_key_local_settles_keyfree():
    ledger = FakeLedger()
    seen = {}

    def key_resolver(scope):
        return "sk-real-key"

    def provider_caller(scope, key):
        seen["key"] = key
        seen["for"] = (scope.business_slug, scope.app_user_id)
        return ({"content": "hi"}, 1800)

    result = handle_provider_request(
        token=_mint(), signing_key=KEY, audience=AUD, now=NOW + 10, nonce_store=InMemoryNonceStore(),
        ledger=ledger, key_resolver=key_resolver, provider_caller=provider_caller, estimate_microusd=2000,
    )
    assert result == {"content": "hi"}            # key-free result
    assert seen["key"] == "sk-real-key"           # key used inside only
    assert seen["for"] == ("climblog", "cust_X")  # validated scope
    assert ("reserve", "climblog", "cust_X", 2000) in ledger.events
    assert ("settle", 1800) in ledger.events


def test_provider_failure_releases_reservation():
    ledger = FakeLedger()

    def boom(scope, key):
        raise RuntimeError("provider 502")

    with pytest.raises(RuntimeError):
        handle_provider_request(
            token=_mint(), signing_key=KEY, audience=AUD, now=NOW + 10, nonce_store=InMemoryNonceStore(),
            ledger=ledger, key_resolver=lambda s: "k", provider_caller=boom, estimate_microusd=2000,
        )
    assert ("release",) in ledger.events
    assert not any(e[0] == "settle" for e in ledger.events)


def test_estimate_over_ceiling_refused_before_reserve():
    ledger = FakeLedger()
    with pytest.raises(BrokerError, match="estimate_exceeds_ceiling"):
        handle_provider_request(
            token=_mint(_scope(max_cost_microusd=1000)), signing_key=KEY, audience=AUD, now=NOW + 10,
            nonce_store=InMemoryNonceStore(), ledger=ledger, key_resolver=lambda s: "k",
            provider_caller=lambda s, k: ({}, 0), estimate_microusd=5000,
        )
    assert ledger.events == []  # never reserved, never called


def test_unconfigured_key_releases_and_raises():
    ledger = FakeLedger()
    with pytest.raises(BrokerError, match="provider_key_unconfigured"):
        handle_provider_request(
            token=_mint(), signing_key=KEY, audience=AUD, now=NOW + 10, nonce_store=InMemoryNonceStore(),
            ledger=ledger, key_resolver=lambda s: "", provider_caller=lambda s, k: ({}, 0), estimate_microusd=2000,
        )
    assert ("release",) in ledger.events
