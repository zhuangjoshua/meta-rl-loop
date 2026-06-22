"""Phase 2 single-use nonce store (deploy/SAFEBOX-BROKER-REMEDIATION-PLAN.md): a token's nonce can be
claimed exactly once, so a replayed token is rejected."""
from plugins.takyon.safebox_nonce import InMemoryNonceStore


def test_first_claim_succeeds_replay_fails():
    store = InMemoryNonceStore()
    assert store.claim("n1", expires_at=100, now=10) is True
    assert store.claim("n1", expires_at=100, now=20) is False  # replay blocked
    assert store.claim("n2", expires_at=100, now=20) is True  # distinct nonce ok


def test_empty_nonce_rejected():
    store = InMemoryNonceStore()
    assert store.claim("", expires_at=100, now=10) is False


def test_expired_nonce_is_pruned_and_reclaimable():
    store = InMemoryNonceStore()
    assert store.claim("n1", expires_at=100, now=10) is True
    assert store.claim("n1", expires_at=100, now=50) is False
    # after expiry the nonce is pruned (harmless — the token itself is expired by then)
    assert store.claim("n1", expires_at=200, now=150) is True
