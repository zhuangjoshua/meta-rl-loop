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


def test_zero_ceiling_refuses_any_positive_estimate_before_reserve():
    """max_cost_microusd is a HARD ceiling: a 0 ceiling means a free action (est==0) only. A positive
    estimate against a 0 ceiling must be refused before any reserve / provider call, NOT skipped."""
    ledger = FakeLedger()
    called = []
    with pytest.raises(BrokerError, match="estimate_exceeds_ceiling"):
        handle_provider_request(
            token=_mint(_scope(max_cost_microusd=0)), signing_key=KEY, audience=AUD, now=NOW + 10,
            nonce_store=InMemoryNonceStore(), ledger=ledger, key_resolver=lambda s: called.append("key") or "k",
            provider_caller=lambda s, k: called.append("call") or ({}, 0), estimate_microusd=5000,
        )
    assert ledger.events == []  # never reserved
    assert not called           # never resolved a key, never called the provider


def test_server_estimate_floor_overrides_a_tiny_client_estimate():
    """The reserve is gated on max(server_estimate, client_estimate): a sub-user that passes a tiny
    client estimate to duck the cap is reserved on the SERVER floor (the provider's own price)."""
    ledger = FakeLedger()
    result = handle_provider_request(
        token=_mint(_scope(max_cost_microusd=5000)), signing_key=KEY, audience=AUD, now=NOW + 10,
        nonce_store=InMemoryNonceStore(), ledger=ledger, key_resolver=lambda s: "k",
        provider_caller=lambda s, k: ({"ok": True}, 4000),
        estimate_microusd=1,            # client lowballs to 1 µUSD
        estimate_fn=lambda s: 4000,     # server floor mirrors the real provider price
    )
    assert result == {"ok": True}
    # Reserved on the server floor, not the client's 1.
    assert ("reserve", "climblog", "cust_X", 4000) in ledger.events
    assert not any(e[0] == "reserve" and e[3] == 1 for e in ledger.events)


def test_server_estimate_floor_over_ceiling_is_refused_even_with_tiny_client_estimate():
    """A tiny client estimate cannot duck the ceiling: the SERVER floor is ceiling-checked, so a
    server estimate above the ceiling is refused before any reserve, even at client estimate 1."""
    ledger = FakeLedger()
    with pytest.raises(BrokerError, match="estimate_exceeds_ceiling"):
        handle_provider_request(
            token=_mint(_scope(max_cost_microusd=1000)), signing_key=KEY, audience=AUD, now=NOW + 10,
            nonce_store=InMemoryNonceStore(), ledger=ledger, key_resolver=lambda s: "k",
            provider_caller=lambda s, k: ({}, 0), estimate_microusd=1, estimate_fn=lambda s: 9000,
        )
    assert ledger.events == []


def test_refused_reserve_does_not_burn_the_token_nonce_so_a_retry_succeeds():
    """The nonce is claimed AFTER the ledger reserve, so a refused/transient-failed reserve does NOT
    permanently burn a pre-minted single-use token: a later retry on the same token succeeds."""
    store = InMemoryNonceStore()
    tok = _mint()

    class _FlakyLedger(FakeLedger):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def reserve(self, scope, est):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient reserve failure")
            return super().reserve(scope, est)

    ledger = _FlakyLedger()
    # First attempt: reserve raises BEFORE the nonce is claimed.
    with pytest.raises(RuntimeError, match="transient reserve failure"):
        handle_provider_request(
            token=tok, signing_key=KEY, audience=AUD, now=NOW + 10, nonce_store=store,
            ledger=ledger, key_resolver=lambda s: "k", provider_caller=lambda s, k: ({"ok": 1}, 100),
            estimate_microusd=200,
        )
    assert ledger.events == []  # nothing reserved, nothing released
    # Retry on the SAME token now succeeds — the nonce was never burned.
    result = handle_provider_request(
        token=tok, signing_key=KEY, audience=AUD, now=NOW + 11, nonce_store=store,
        ledger=ledger, key_resolver=lambda s: "k", provider_caller=lambda s, k: ({"ok": 1}, 100),
        estimate_microusd=200,
    )
    assert result == {"ok": 1}
    assert ("reserve", "climblog", "cust_X", 200) in ledger.events
    assert ("settle", 100) in ledger.events


def test_post_reserve_replay_releases_the_hold_and_refuses():
    """If a token survives to the broker but its nonce was already claimed (replay), the reserve made
    just before the claim is RELEASED and the call is refused — no orphaned hold, no provider call."""
    store = InMemoryNonceStore()
    tok = _mint()
    # Burn the token's nonce via a first successful broker.
    first = FakeLedger()
    handle_provider_request(
        token=tok, signing_key=KEY, audience=AUD, now=NOW + 10, nonce_store=store,
        ledger=first, key_resolver=lambda s: "k", provider_caller=lambda s, k: ({"ok": 1}, 100),
        estimate_microusd=200,
    )
    # Replay the SAME token: it reserves, then the nonce claim fails -> release + refuse.
    ledger = FakeLedger()
    called = []
    with pytest.raises(BrokerError, match="replayed_token"):
        handle_provider_request(
            token=tok, signing_key=KEY, audience=AUD, now=NOW + 11, nonce_store=store,
            ledger=ledger, key_resolver=lambda s: called.append("key") or "k",
            provider_caller=lambda s, k: called.append("call") or ({}, 0), estimate_microusd=200,
        )
    assert ("reserve", "climblog", "cust_X", 200) in ledger.events
    assert ("release",) in ledger.events
    assert not any(e[0] == "settle" for e in ledger.events)
    assert not called  # never resolved a key, never called the provider on the replay
