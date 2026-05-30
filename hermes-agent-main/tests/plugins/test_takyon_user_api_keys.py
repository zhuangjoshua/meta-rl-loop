from __future__ import annotations

import hashlib

from plugins.takyon.user_api_keys import (
    API_KEY_PREFIX,
    generate_api_key,
    hash_api_key,
    is_well_formed,
    key_prefix,
    verify_api_key,
)


def test_generated_key_is_prefixed_and_well_formed():
    raw = generate_api_key()
    assert raw.startswith(API_KEY_PREFIX)
    assert is_well_formed(raw)


def test_generated_keys_are_unique():
    keys = {generate_api_key() for _ in range(2000)}
    assert len(keys) == 2000


def test_hash_is_deterministic():
    raw = generate_api_key()
    assert hash_api_key(raw) == hash_api_key(raw)


def test_hash_differs_per_key():
    assert hash_api_key(generate_api_key()) != hash_api_key(generate_api_key())


def test_hash_is_sha256_hex():
    raw = generate_api_key()
    digest = hash_api_key(raw)
    assert digest == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_verify_true_for_matching_key():
    raw = generate_api_key()
    assert verify_api_key(raw, hash_api_key(raw)) is True


def test_verify_false_for_different_key():
    a = generate_api_key()
    b = generate_api_key()
    assert verify_api_key(b, hash_api_key(a)) is False


def test_verify_false_for_tampered_hash():
    raw = generate_api_key()
    digest = hash_api_key(raw)
    tampered = ("0" if digest[0] != "0" else "1") + digest[1:]
    assert verify_api_key(raw, tampered) is False


def test_verify_false_for_empty_inputs():
    raw = generate_api_key()
    assert verify_api_key("", hash_api_key(raw)) is False
    assert verify_api_key(raw, "") is False
    assert verify_api_key("", "") is False


def test_prefix_is_non_secret_and_insufficient_to_verify():
    raw = generate_api_key()
    prefix = key_prefix(raw)
    assert raw.startswith(prefix)
    assert len(prefix) < len(raw)
    # The stored, displayable prefix must never satisfy verification.
    assert verify_api_key(prefix, hash_api_key(raw)) is False


def test_prefix_is_stable_for_same_key():
    raw = generate_api_key()
    assert key_prefix(raw) == key_prefix(raw)


def test_is_well_formed_rejects_bad_inputs():
    assert is_well_formed("") is False
    assert is_well_formed("nope_missing_prefix") is False
    assert is_well_formed(API_KEY_PREFIX) is False
    assert is_well_formed(API_KEY_PREFIX + "short") is False
    assert is_well_formed(API_KEY_PREFIX + "x" * 40 + "!@#") is False
    assert is_well_formed(None) is False  # type: ignore[arg-type]


def test_raw_key_is_not_its_hash():
    raw = generate_api_key()
    assert hash_api_key(raw) != raw
