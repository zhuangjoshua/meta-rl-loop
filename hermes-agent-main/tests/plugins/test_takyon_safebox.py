from __future__ import annotations

import json
import os
import uuid

import pytest
from starlette.testclient import TestClient

from plugins.takyon import safebox
from plugins.takyon.safebox_app import build_safebox_app
from plugins.takyon.stripe_util import build_signature_header
from plugins.takyon.user_api_keys import generate_api_key


def test_read_env_backed_value_prefers_process_env(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_process")
    monkeypatch.setattr(
        safebox,
        "load_env",
        lambda: {"STRIPE_SECRET_KEY": "sk_disk_fallback"},
    )

    assert safebox.read_env_backed_value("STRIPE_SECRET_KEY") == "sk_live_process"


def test_read_env_backed_value_falls_back_to_takyon_env(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setattr(
        safebox,
        "load_env",
        lambda: {"STRIPE_SECRET_KEY": "sk_disk_fallback"},
    )

    assert safebox.read_env_backed_value("STRIPE_SECRET_KEY") == "sk_disk_fallback"


def test_first_env_backed_value_returns_first_populated_alias(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.setattr(
        safebox,
        "load_env",
        lambda: {"POSTGRES_URL": "postgres://from-disk"},
    )

    assert (
        safebox.first_env_backed_value("DATABASE_URL", "POSTGRES_URL")
        == "postgres://from-disk"
    )


def test_first_env_backed_value_survives_unreadable_env(monkeypatch):
    # The .env file can be momentarily unreadable (a concurrent root-run secret write leaves it
    # briefly root-owned 0600). os.environ — which systemd loads from .env and _save_env_value_direct
    # keeps in sync — is authoritative, so a non-sensitive alias (DATABASE_URL) must still resolve
    # rather than 500ing /v1/env/first for every business.
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("DATABASE_URL", "postgres://from-process-env")

    def _boom():
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(safebox, "load_env", _boom)

    assert (
        safebox.first_env_backed_value("DATABASE_URL", "POSTGRES_URL")
        == "postgres://from-process-env"
    )


def test_read_env_backed_value_allows_sensitive_api_keys(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        safebox,
        "load_env",
        lambda: {"OPENAI_API_KEY": "sk-openai-disk"},
    )

    assert safebox.read_env_backed_value("OPENAI_API_KEY") == "sk-openai-disk"


def test_read_env_backed_value_rejects_non_sensitive_keys(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    with pytest.raises(KeyError, match="non-sensitive env key"):
        safebox.read_env_backed_value("OPENAI_BASE_URL")


def test_read_env_backed_value_requires_remote_or_safebox_host(monkeypatch):
    monkeypatch.delenv("TAKYON_HOST_ROLE", raising=False)
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)

    with pytest.raises(safebox.SafeboxAuthorityUnavailable, match="TAKYON_SAFEBOX_URL"):
        safebox.read_env_backed_value("OPENAI_API_KEY")


def test_save_and_remove_env_backed_value_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    safebox.save_env_backed_value("OPENAI_API_KEY", "sk-round-trip")

    env_path = tmp_path / ".env"
    assert env_path.exists()
    assert "OPENAI_API_KEY=sk-round-trip" in env_path.read_text(encoding="utf-8")
    assert os.environ["OPENAI_API_KEY"] == "sk-round-trip"

    removed = safebox.remove_env_backed_value("OPENAI_API_KEY")

    assert removed is True
    assert "OPENAI_API_KEY" not in env_path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in os.environ


def test_user_api_key_round_trip_is_safebox_owned(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")

    raw = generate_api_key()
    record = safebox.register_user_api_key("user-1", raw, key_id=str(uuid.uuid4()))

    resolved = safebox.resolve_user_api_key(raw)

    assert resolved is not None
    assert resolved["id"] == record["id"]
    assert resolved["user_id"] == "user-1"
    assert resolved["prefix"] == record["prefix"]
    assert resolved["key_hash"] != raw


def test_user_api_key_revoke_blocks_future_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")

    raw = generate_api_key()
    record = safebox.register_user_api_key("user-1", raw, key_id=str(uuid.uuid4()))
    assert safebox.resolve_user_api_key(raw) is not None

    assert safebox.revoke_user_api_key(str(record["id"])) is True
    assert safebox.resolve_user_api_key(raw) is None


def test_user_api_key_registry_blocks_second_active_key_for_one_user(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")

    safebox.register_user_api_key("user-1", generate_api_key(), key_id=str(uuid.uuid4()))

    with pytest.raises(ValueError, match="active user api key already exists"):
        safebox.register_user_api_key(
            "user-1",
            generate_api_key(),
            key_id=str(uuid.uuid4()),
        )


def test_remote_safebox_env_reads_delegate_to_service(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")
    calls: list[tuple[str, str, dict | None]] = []

    def _fake_remote(method: str, path: str, payload=None):
        calls.append((method, path, payload))
        return {"value": "sk-remote"}

    monkeypatch.setattr(safebox, "_remote_json", _fake_remote)

    assert safebox.read_env_backed_value("OPENAI_API_KEY") == "sk-remote"
    assert calls == [("GET", "/v1/env/OPENAI_API_KEY", None)]


def test_remote_safebox_user_key_register_delegates_to_service(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")
    raw = generate_api_key()
    key_id = str(uuid.uuid4())
    calls: list[tuple[str, str, dict | None]] = []

    def _fake_remote(method: str, path: str, payload=None):
        calls.append((method, path, payload))
        return {"record": {"id": key_id, "user_id": "user-1"}}

    monkeypatch.setattr(safebox, "_remote_json", _fake_remote)

    record = safebox.register_user_api_key("user-1", raw, key_id=key_id)

    assert record == {"id": key_id, "user_id": "user-1"}
    assert calls == [
        (
            "POST",
            "/v1/user-api-keys/register",
            {
                "user_id": "user-1",
                "raw_key": raw,
                "key_id": key_id,
                "created_at": None,
            },
        )
    ]


def test_remote_safebox_creative_credit_balance_delegates_to_service(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")
    calls: list[tuple[str, str, dict | None]] = []

    def _fake_remote(method: str, path: str, payload=None):
        calls.append((method, path, payload))
        return {
            "business_slug": "acme",
            "balance_credits": 7,
            "reserved_credits": 2,
        }

    monkeypatch.setattr(safebox, "_remote_json", _fake_remote)

    balances = safebox.get_business_credit_balances(None, "acme")

    assert balances.business_slug == "acme"
    assert balances.balance_credits == 7
    assert balances.reserved_credits == 2
    assert calls == [("GET", "/v1/creative-credits/acme", None)]


def test_remote_safebox_creative_credit_checkout_delegates_to_service(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")
    calls: list[tuple[str, str, dict | None]] = []

    def _fake_remote(method: str, path: str, payload=None):
        calls.append((method, path, payload))
        return {
            "checkout_url": "https://checkout.stripe.test/cs_123",
            "session_id": "cs_123",
            "business_slug": "acme",
            "credits": 125,
            "amount_cents": 125,
            "price_cents_per_credit": 1,
        }

    monkeypatch.setattr(safebox, "_remote_json", _fake_remote)

    payload = safebox.create_creative_credit_checkout(
        "user-1",
        "acme",
        credits=125,
        success_url="https://app.example.com/#/app/c/acme",
        cancel_url="https://app.example.com/#/app/c/acme",
    )

    assert payload["session_id"] == "cs_123"
    assert calls == [
        (
            "POST",
            "/v1/creative-credits/checkout",
            {
                "user_id": "user-1",
                "business_slug": "acme",
                "credits": 125,
                "pack_id": None,
                "success_url": "https://app.example.com/#/app/c/acme",
                "cancel_url": "https://app.example.com/#/app/c/acme",
            },
        )
    ]


def test_remote_safebox_creative_credit_reserve_maps_insufficient_credits(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")

    def _fake_remote(method: str, path: str, payload=None):
        raise safebox.RemoteSafeboxError(
            "blocked",
            status_code=402,
            payload={
                "detail": {
                    "error": "insufficient_creative_credits: need 5, have 3",
                    "requested_credits": 5,
                    "available_credits": 3,
                }
            },
        )

    monkeypatch.setattr(safebox, "_remote_json", _fake_remote)

    with pytest.raises(safebox.InsufficientCreativeCredits) as exc:
        safebox.reserve_credits(None, "acme", 5, "resv-1")

    assert exc.value.requested_credits == 5
    assert exc.value.available_credits == 3


def test_creative_credit_access_requires_remote_or_safebox_host(monkeypatch):
    monkeypatch.delenv("TAKYON_HOST_ROLE", raising=False)
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)

    with pytest.raises(safebox.SafeboxAuthorityUnavailable, match="TAKYON_SAFEBOX_URL"):
        safebox.get_business_credit_balances(None, "acme")


def test_safebox_app_fails_closed_when_token_is_unconfigured(tmp_path, monkeypatch):
    """A missing TAKYON_SAFEBOX_TOKEN must mean 401-everything, never auth-disabled — Safebox
    safety must not silently degrade to firewall/VPC correctness."""
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("TAKYON_SAFEBOX_TOKEN", raising=False)

    client = TestClient(build_safebox_app())

    assert client.get("/healthz").status_code == 200
    no_token = client.get("/v1/env/OPENAI_API_KEY")
    assert no_token.status_code == 401
    any_bearer = client.get(
        "/v1/env/OPENAI_API_KEY", headers={"Authorization": "Bearer anything"}
    )
    assert any_bearer.status_code == 401

    # Local test rigs may opt out EXPLICITLY (hermetic pytest envs scrub *_TOKEN vars).
    monkeypatch.setenv("TAKYON_SAFEBOX_ALLOW_TOKENLESS", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    allowed = client.get("/v1/env/OPENAI_API_KEY")
    assert allowed.status_code == 200


def test_safebox_app_requires_internal_token_and_round_trips_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    client = TestClient(build_safebox_app())

    unauthorized = client.get("/v1/env/OPENAI_API_KEY")
    assert unauthorized.status_code == 401

    headers = {"Authorization": "Bearer shared-token"}
    saved = client.post("/v1/env/OPENAI_API_KEY", json={"value": "sk-live"}, headers=headers)
    assert saved.status_code == 200
    assert saved.json() == {"ok": True}

    read_back = client.get("/v1/env/OPENAI_API_KEY", headers=headers)
    assert read_back.status_code == 200
    assert read_back.json() == {"value": "sk-live"}


def test_safebox_app_requires_internal_token_and_reads_creative_credit_balance(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    monkeypatch.setattr(
        safebox,
        "_local_get_business_credit_balances",
        lambda conn, business_slug: safebox.CreativeCreditBalances(
            business_slug=business_slug,
            balance_credits=9,
            reserved_credits=4,
        ),
    )

    client = TestClient(build_safebox_app())

    unauthorized = client.get("/v1/creative-credits/acme")
    assert unauthorized.status_code == 401

    headers = {"Authorization": "Bearer shared-token"}
    read_back = client.get("/v1/creative-credits/acme", headers=headers)
    assert read_back.status_code == 200
    assert read_back.json() == {
        "business_slug": "acme",
        "balance_credits": 9,
        "reserved_credits": 4,
    }


def test_safebox_app_requires_internal_token_and_creates_creative_credit_checkout(monkeypatch):
    from plugins.takyon import control_api

    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    monkeypatch.setattr(
        control_api,
        "create_creative_credit_checkout_session",
        lambda user_id, business_slug, **kwargs: (
            {"id": "cs_credit_1", "url": "https://checkout.stripe.test/cs_credit_1"},
            {
                "pack_id": None,
                "credits": 125,
                "amount_cents": 125,
                "price_cents_per_credit": 1,
            },
        ),
    )

    client = TestClient(build_safebox_app())

    unauthorized = client.post(
        "/v1/creative-credits/checkout",
        json={
            "user_id": "user-1",
            "business_slug": "acme",
            "credits": 125,
            "success_url": "https://app.example.com/#/app/c/acme",
            "cancel_url": "https://app.example.com/#/app/c/acme",
        },
    )
    assert unauthorized.status_code == 401

    headers = {"Authorization": "Bearer shared-token"}
    response = client.post(
        "/v1/creative-credits/checkout",
        headers=headers,
        json={
            "user_id": "user-1",
            "business_slug": "acme",
            "credits": 125,
            "success_url": "https://app.example.com/#/app/c/acme",
            "cancel_url": "https://app.example.com/#/app/c/acme",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "checkout_url": "https://checkout.stripe.test/cs_credit_1",
        "session_id": "cs_credit_1",
        "business_slug": "acme",
        "pack_id": None,
        "credits": 125,
        "amount_cents": 125,
        "price_cents_per_credit": 1,
    }


def test_safebox_app_verifies_billing_webhook_signature(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    monkeypatch.setenv("STRIPE_BILLING_WEBHOOK_SECRET", "whsec_test_xyz")

    client = TestClient(build_safebox_app())
    body = json.dumps({"id": "evt_123", "type": "checkout.session.completed"})
    headers = {"Authorization": "Bearer shared-token"}

    unauthorized = client.post(
        "/v1/stripe/billing-webhook/verify",
        json={"raw_body": body, "signature": build_signature_header(body, "whsec_test_xyz")},
    )
    assert unauthorized.status_code == 401

    response = client.post(
        "/v1/stripe/billing-webhook/verify",
        headers=headers,
        json={"raw_body": body, "signature": build_signature_header(body, "whsec_test_xyz")},
    )
    assert response.status_code == 200
    assert response.json() == {
        "event": {"id": "evt_123", "type": "checkout.session.completed"}
    }
