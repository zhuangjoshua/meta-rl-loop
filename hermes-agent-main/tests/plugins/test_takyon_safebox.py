from __future__ import annotations

import json
import os
import uuid

import pytest
from starlette.testclient import TestClient

from plugins.takyon import safebox
from plugins.takyon import core as takyon_core
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


def test_read_env_backed_value_uses_managed_secret_command_for_manifested_key(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_MANAGED_SECRET_KEYS", "STRIPE_SECRET_KEY")
    monkeypatch.setenv("TAKYON_MANAGED_SECRET_COMMAND", "secretctl read {key}")
    monkeypatch.setenv("TAKYON_MANAGED_SECRET_CACHE_SECONDS", "0")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_plaintext_should_not_win")
    monkeypatch.setattr(
        safebox,
        "load_env",
        lambda: {"STRIPE_SECRET_KEY": "sk_disk_should_not_win"},
    )
    safebox._MANAGED_SECRET_CACHE.clear()
    seen = {}

    class Completed:
        returncode = 0
        stdout = "sk_managed\n"

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(safebox.subprocess, "run", fake_run)

    assert safebox.read_env_backed_value("STRIPE_SECRET_KEY") == "sk_managed"
    assert seen["argv"] == ["secretctl", "read", "STRIPE_SECRET_KEY"]
    assert seen["kwargs"]["check"] is False
    assert seen["kwargs"]["capture_output"] is True
    assert seen["kwargs"]["text"] is True


def test_read_env_backed_value_keeps_env_fallback_for_unmanifested_key(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_MANAGED_SECRET_KEYS", "OPENAI_API_KEY")
    monkeypatch.setenv("TAKYON_MANAGED_SECRET_COMMAND", "secretctl read {key}")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_process")
    monkeypatch.setattr(safebox, "load_env", lambda: {})

    def fake_run(*_args, **_kwargs):  # pragma: no cover - should not be called
        raise AssertionError("managed command should not run for unmanifested keys")

    monkeypatch.setattr(safebox.subprocess, "run", fake_run)

    assert safebox.read_env_backed_value("STRIPE_SECRET_KEY") == "sk_process"


def test_save_env_backed_value_refuses_manager_owned_key(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_MANAGED_SECRET_KEYS", "STRIPE_SECRET_KEY")
    monkeypatch.setenv("TAKYON_MANAGED_SECRET_COMMAND", "secretctl read {key}")

    with pytest.raises(safebox.ManagedSecretLookupError, match="managed secret store"):
        safebox.save_env_backed_value("STRIPE_SECRET_KEY", "sk_should_not_write_env")


def test_list_env_backed_keys_includes_managed_manifest(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_MANAGED_SECRET_KEYS", "STRIPE_SECRET_KEY, OPENAI_API_KEY")
    monkeypatch.setenv("TAKYON_MANAGED_SECRET_COMMAND", "secretctl read {key}")
    monkeypatch.setattr(safebox, "load_env", lambda: {})

    keys = safebox.list_env_backed_keys(sensitive_only=False)

    assert "STRIPE_SECRET_KEY" in keys
    assert "OPENAI_API_KEY" in keys


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


def test_auth0_login_state_and_session_verify_are_safebox_owned(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("AUTH0_SECRET", "cookie-signing-secret")
    monkeypatch.setenv("ARGON_BETA_ALLOWED_EMAIL_DOMAINS", "fourmanifold.com")

    login_state = safebox.auth0_login_state(
        state="state-1",
        nonce="nonce-1",
        return_to="/chat",
        issued_at=1000,
    )

    assert set(login_state) >= {"state_token", "nonce_token", "return_to"}
    assert login_state["return_to"] == "/chat"
    assert "cookie-signing-secret" not in json.dumps(login_state)

    user = {
        "sub": "auth0|operator",
        "email": "operator@fourmanifold.com",
        "name": "Operator",
        "email_verified": True,
    }
    session = safebox._auth0_sign_payload(  # type: ignore[attr-defined]
        "cookie-signing-secret",
        {**user, "iat": 1001, "exp": 2000},
    )

    assert safebox.auth0_verify_session(session_token=session, now=1500)["email"] == user["email"]
    assert safebox.auth0_verify_session(session_token="not-a-session", now=1500) is None


def test_auth0_callback_route_signs_session_after_safebox_verification(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "test-token")
    # /v1/auth0/* routes are operator-authority: they need the route-specific operator token AND an
    # explicit client allowlist on top of the shared transport token.
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_TOKEN", "operator-route-token")
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_CLIENTS", "testclient")
    monkeypatch.setenv("AUTH0_SECRET", "cookie-signing-secret")
    monkeypatch.setenv("AUTH0_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("AUTH0_DOMAIN", "example.us.auth0.com")
    monkeypatch.setenv("AUTH0_CLIENT_ID", "client-id")
    monkeypatch.setenv("ARGON_BETA_ALLOWED_EMAIL_DOMAINS", "fourmanifold.com")

    login_state = safebox.auth0_login_state(
        state="state-1",
        nonce="nonce-1",
        return_to="/chat",
        issued_at=1000,
    )

    def fake_exchange(*, code: str, redirect_uri: str):
        assert code == "ok"
        assert redirect_uri == "https://app.example.com/auth/callback"
        return {"id_token": "id-token"}

    def fake_verify(*, id_token: str, expected_nonce: str):
        assert id_token == "id-token"
        assert expected_nonce == "nonce-1"
        return {
            "sub": "auth0|operator",
            "email": "operator@fourmanifold.com",
            "email_verified": True,
            "name": "Operator",
        }

    monkeypatch.setattr(safebox, "_auth0_exchange_code", fake_exchange)
    monkeypatch.setattr(safebox, "_auth0_verify_id_token", fake_verify)

    client = TestClient(build_safebox_app())
    headers = {
        "Authorization": "Bearer test-token",
        "X-Takyon-Operator-Token": "operator-route-token",
    }
    resp = client.post(
        "/v1/auth0/callback",
        headers=headers,
        json={
            "code": "ok",
            "state": "state-1",
            "state_token": login_state["state_token"],
            "nonce_token": login_state["nonce_token"],
            "redirect_uri": "https://app.example.com/auth/callback",
            "now": 1005,
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["email"] == "operator@fourmanifold.com"
    assert body["return_to"] == "/chat"
    assert "cookie-signing-secret" not in resp.text
    verify = client.post(
        "/v1/auth0/session/verify",
        headers=headers,
        json={"session_token": body["session_token"], "now": 1010},
    )
    assert verify.status_code == 200
    assert verify.json()["authenticated"] is True


def test_auth0_callback_route_rejects_disallowed_email(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "test-token")
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_TOKEN", "operator-route-token")
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_CLIENTS", "testclient")
    monkeypatch.setenv("AUTH0_SECRET", "cookie-signing-secret")
    monkeypatch.setenv("AUTH0_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("AUTH0_DOMAIN", "example.us.auth0.com")
    monkeypatch.setenv("AUTH0_CLIENT_ID", "client-id")
    monkeypatch.setenv("ARGON_BETA_ALLOWED_EMAIL_DOMAINS", "fourmanifold.com")

    login_state = safebox.auth0_login_state(
        state="state-1",
        nonce="nonce-1",
        return_to="/chat",
        issued_at=1000,
    )
    monkeypatch.setattr(safebox, "_auth0_exchange_code", lambda **_kwargs: {"id_token": "id-token"})
    monkeypatch.setattr(
        safebox,
        "_auth0_verify_id_token",
        lambda **_kwargs: {
            "sub": "auth0|outsider",
            "email": "someone@example.com",
            "email_verified": True,
            "name": "Outsider",
        },
    )

    client = TestClient(build_safebox_app())
    resp = client.post(
        "/v1/auth0/callback",
        headers={
            "Authorization": "Bearer test-token",
            "X-Takyon-Operator-Token": "operator-route-token",
        },
        json={
            "code": "ok",
            "state": "state-1",
            "state_token": login_state["state_token"],
            "nonce_token": login_state["nonce_token"],
            "redirect_uri": "https://app.example.com/auth/callback",
            "now": 1005,
        },
    )

    assert resp.status_code == 403
    assert "not allowed" in resp.text


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


def test_dataforseo_credentials_are_safebox_sensitive():
    assert safebox.is_sensitive_env_key("DATAFORSEO_LOGIN") is True
    assert safebox.is_sensitive_env_key("DATAFORSEO_PASSWORD") is True


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


def test_remote_safebox_business_bootstrap_credit_delegates_fixed_policy(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")
    calls: list[tuple[str, str, dict | None]] = []

    def _fake_remote(method: str, path: str, payload=None):
        calls.append((method, path, payload))
        return {
            "business_slug": "acme",
            "balance_credits": 3,
            "reserved_credits": 0,
            "credited_credits": 3,
        }

    monkeypatch.setattr(safebox, "_remote_json", _fake_remote)

    balances = safebox.grant_business_bootstrap_credits(None, "acme", "user-1")

    assert balances.business_slug == "acme"
    assert balances.balance_credits == 3
    assert balances.reserved_credits == 0
    assert calls == [
        (
            "POST",
            "/v1/creative-credits/bootstrap-starter",
            {"business_slug": "acme", "operator_user_id": "user-1"},
        )
    ]


def test_remote_safebox_creative_gate_metadata_passthrough(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")
    calls: list[tuple[str, str, dict | None]] = []

    def _fake_remote(method: str, path: str, payload=None, **kwargs):
        calls.append((method, path, payload))
        if path == "/v1/creative/reserve":
            return {
                "token": "cap-token",
                "audience": "creative.x_publish",
                "reservation_key": payload["reservation_key"],
                "reserved_credits": 1,
                "credits": 1,
            }
        return {
            "business_slug": "acme",
            "balance_credits": 2,
            "reserved_credits": 0,
        }

    monkeypatch.setattr(safebox, "_remote_json", _fake_remote)

    reserve = safebox.creative_reserve(
        business="acme",
        operator_user_id="user-1",
        action="creative.x_publish",
        reservation_key="rk-x",
        metadata={"budget_bucket": "x"},
    )
    committed = safebox.creative_commit(
        reservation_key="rk-x",
        actual_credits=1,
        metadata={"status": "posted"},
    )
    released = safebox.creative_release(
        reservation_key="rk-y",
        metadata={"status": "failed"},
    )

    assert reserve["token"] == "cap-token"
    assert committed.balance_credits == 2
    assert released.balance_credits == 2
    assert calls == [
        (
            "POST",
            "/v1/creative/reserve",
            {
                "business": "acme",
                "operator_user_id": "user-1",
                "action": "creative.x_publish",
                "reservation_key": "rk-x",
                "units": 1,
                "metadata": {"budget_bucket": "x"},
            },
        ),
        (
            "POST",
            "/v1/creative/commit",
            {
                "reservation_key": "rk-x",
                "actual_credits": 1,
                "metadata": {"status": "posted"},
            },
        ),
        (
            "POST",
            "/v1/creative/release",
            {
                "reservation_key": "rk-y",
                "metadata": {"status": "failed"},
            },
        ),
    ]


def test_remote_safebox_creative_credit_grant_refuses_arbitrary_amount(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")

    with pytest.raises(safebox.SafeboxAuthorityUnavailable, match="verified checkout/webhook"):
        safebox.grant_credits(None, "acme", 999999, "attacker-grant")


def test_remote_safebox_billing_and_custody_open_delegate_to_service(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")
    calls: list[tuple[str, str, dict | None]] = []

    def _fake_remote(method: str, path: str, payload=None):
        calls.append((method, path, payload))
        return {"ok": True}

    monkeypatch.setattr(safebox, "_remote_json", _fake_remote)

    safebox.open_billing_account(None, "user-1")
    safebox.open_custody_account(None, "user-1", currency="usd")

    assert calls == [
        ("POST", "/v1/billing/accounts/open", {"user_id": "user-1", "allowance_included_cents": 0}),
        ("POST", "/v1/custody/accounts/open", {"user_id": "user-1", "currency": "usd"}),
    ]


def test_remote_safebox_operator_billing_ledger_delegates_to_service(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")
    calls: list[tuple[str, str, dict | None]] = []

    def _fake_remote(method: str, path: str, payload=None, **kwargs):
        calls.append((method, path, payload))
        if path == "/v1/billing/reserve":
            return {"reservation_key": payload["reservation_key"], "allowance_cents": 25}
        if path == "/v1/billing/balances":
            return {
                "user_id": payload["user_id"],
                "allowance_included_cents": 100,
                "allowance_used_cents": 25,
                "allowance_remaining_cents": 75,
                "reserved_cents": 0,
            }
        return {"ok": True}

    monkeypatch.setattr(safebox, "_remote_json", _fake_remote)

    assert safebox.billing_reserve(
        None,
        "user-1",
        25,
        "rk-1",
        business_slug="acme",
        job_id="job-1",
    ) == {"reservation_key": "rk-1", "allowance_cents": 25}
    safebox.billing_settle(None, "rk-1", 20)
    safebox.billing_refund(None, "rk-2")
    assert safebox.billing_balances(None, "user-1")["allowance_remaining_cents"] == 75

    assert calls == [
        (
            "POST",
            "/v1/billing/reserve",
            {
                "user_id": "user-1",
                "estimate_cents": 25,
                "reservation_key": "rk-1",
                "business_slug": "acme",
                "job_id": "job-1",
            },
        ),
        ("POST", "/v1/billing/settle", {"reservation_key": "rk-1", "actual_cents": 20}),
        ("POST", "/v1/billing/refund", {"reservation_key": "rk-2"}),
        ("POST", "/v1/billing/balances", {"user_id": "user-1"}),
    ]


def test_billing_reserve_uses_remote_safebox_on_runtime_plane(monkeypatch):
    from plugins.takyon import billing

    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")
    monkeypatch.setenv("TAKYON_HOST_ROLE", "operator")
    calls: list[tuple[str, int, str]] = []

    def _fake_reserve(conn, user_id, estimate_cents, reservation_key, **kwargs):
        calls.append((user_id, estimate_cents, reservation_key))
        return {"reservation_key": reservation_key, "allowance_cents": 17}

    monkeypatch.setattr(safebox, "billing_reserve", _fake_reserve)

    res = billing.reserve(None, "user-1", 17, "rk-runtime")

    assert (res.key, res.allowance_cents) == ("rk-runtime", 17)
    assert calls == [("user-1", 17, "rk-runtime")]


def test_remote_safebox_signed_webhook_processors_delegate_to_process_routes(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")
    calls: list[tuple[str, str, dict | None]] = []

    def _fake_remote(method: str, path: str, payload=None, **kwargs):
        calls.append((method, path, payload))
        return {"ok": True, "provider_event_id": "evt_1", "type": "checkout.session.completed"}

    monkeypatch.setattr(safebox, "_remote_json", _fake_remote)

    assert safebox.process_stripe_billing_webhook("{}", "sig")["ok"] is True
    assert safebox.process_stripe_app_webhook("{}", "sig")["ok"] is True
    assert calls == [
        ("POST", "/v1/billing/webhook/process", {"raw_body": "{}", "signature": "sig"}),
        ("POST", "/v1/stripe/app-webhook/process", {"raw_body": "{}", "signature": "sig"}),
    ]


def test_remote_safebox_app_checkout_reconcile_delegates_to_service(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")
    calls: list[tuple[str, str, dict | None]] = []

    def _fake_remote(method: str, path: str, payload=None, **kwargs):
        calls.append((method, path, payload))
        return {
            "ok": True,
            "session_id": "cs_paid_1",
            "business_slug": "acme",
            "processed": {"recorded": True, "business_slug": "acme"},
            "subscription": None,
        }

    monkeypatch.setattr(safebox, "_remote_json", _fake_remote)

    result = safebox.reconcile_app_checkout_session(
        None,
        session_id="cs_paid_1",
        expected_business_slug="acme",
        app_user_id="app-user-1",
        customer_email="buyer@example.com",
    )

    assert result["processed"]["recorded"] is True
    assert calls == [
        (
            "POST",
            "/v1/stripe/app-checkout/reconcile",
            {
                "session_id": "cs_paid_1",
                "business_slug": "acme",
                "app_user_id": "app-user-1",
                "customer_email": "buyer@example.com",
            },
        )
    ]


def test_remote_safebox_app_checkout_reconcile_requires_expected_context(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")
    monkeypatch.setattr(safebox, "_remote_json", lambda *a, **k: pytest.fail("remote called"))

    with pytest.raises(ValueError, match="expected business and app user/email context"):
        safebox.reconcile_app_checkout_session(None, session_id="cs_paid_1")


def test_pg_checkout_recovery_uses_safebox_reconcile_when_remote(monkeypatch):
    class _Rows:
        def __init__(self, *, one=None, many=None):
            self._one = one
            self._many = many or []

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._many

    class _FakePG:
        def execute(self, sql, params=None):
            normalized = sql.lower()
            if "from app_entitlements" in normalized:
                return _Rows(one=None)
            if "from app_checkout_intents" in normalized:
                return _Rows(many=[{"stripe_checkout_session_id": "cs_paid_1"}])
            raise AssertionError(sql)

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    calls: list[dict[str, str | None]] = []

    def _fake_reconcile(conn, *, session_id, expected_business_slug=None, app_user_id=None, customer_email=None):
        calls.append(
            {
                "session_id": session_id,
                "expected_business_slug": expected_business_slug,
                "app_user_id": app_user_id,
                "customer_email": customer_email,
            }
        )
        return {
            "ok": True,
            "processed": {
                "recorded": True,
                "business_slug": expected_business_slug,
                "app_user_id": app_user_id,
            },
            "subscription": {"recorded": True},
        }

    monkeypatch.setattr(safebox, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(safebox, "reconcile_app_checkout_session", _fake_reconcile)

    class _OperatorPlaneStore:
        # Recovery only runs for operator-plane stores; an app-plane read never reconciles checkout.
        _database_plane = "operator"

    conn = takyon_core._PGConn(_FakePG())
    result = takyon_core._maybe_reconcile_pg_completed_checkout(
        _OperatorPlaneStore(),
        conn,
        "acme",
        {"id": "app-user-1", "email": "Buyer@Example.com"},
    )

    assert result == {
        "attempted": True,
        "session_id": "cs_paid_1",
        "checkout": {"recorded": True, "business_slug": "acme", "app_user_id": "app-user-1"},
        "subscription": {"recorded": True},
    }
    assert calls == [
        {
            "session_id": "cs_paid_1",
            "expected_business_slug": "acme",
            "app_user_id": "app-user-1",
            "customer_email": "buyer@example.com",
        }
    ]


def test_remote_safebox_creative_credit_reserve_maps_insufficient_credits(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")

    # A runtime-plane direct reserve is a CLOSED rail: creative spend must route through the
    # authoritative safebox creative gate (/v1/creative/reserve), never a runtime-side reservation.
    with pytest.raises(safebox.CreativeGateRefused, match="creative_credit_spend_requires_creative_gate"):
        safebox.reserve_credits(None, "acme", 5, "resv-1")

    # The gate client surfaces the safebox's structured insufficient-credits refusal (402 with
    # requested/available detail) so callers can still report exactly what was missing.
    def _fake_remote(method: str, path: str, payload=None, **_kwargs):
        assert (method, path) == ("POST", "/v1/creative/reserve")
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

    with pytest.raises(safebox.CreativeGateRefused) as exc:
        safebox.creative_reserve(
            business="acme",
            operator_user_id="user-1",
            action="business_generate_logo",
            reservation_key="resv-1",
        )

    assert exc.value.status_code == 402
    assert exc.value.payload["requested_credits"] == 5
    assert exc.value.payload["available_credits"] == 3


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

    # Local test rigs may opt out EXPLICITLY (hermetic pytest envs scrub *_TOKEN vars). Once auth is
    # bypassed, an ALLOWLISTED infra config name still serves over /v1/env (a provider key or DB DSN
    # would 404 — see the egress-allowlist test below).
    monkeypatch.setenv("TAKYON_SAFEBOX_ALLOW_TOKENLESS", "1")
    monkeypatch.setenv("POSTMARK_FROM_EMAIL", "hello@infra-serves.example")
    allowed = client.get("/v1/env/POSTMARK_FROM_EMAIL")
    assert allowed.status_code == 200
    assert allowed.json() == {"value": "hello@infra-serves.example"}


def test_safebox_app_requires_internal_token_and_serves_infra_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    monkeypatch.setenv("AUTH0_DOMAIN", "provisioned-on-host.us.auth0.com")
    monkeypatch.setenv("DATABASE_URL", "postgres://provisioned-on-host")

    client = TestClient(build_safebox_app())

    # Auth required.
    assert client.get("/v1/env/AUTH0_DOMAIN").status_code == 401

    headers = {"Authorization": "Bearer shared-token"}
    # Allowlisted infra config (provisioned out-of-band on the safebox host) READS back over /v1/env.
    read_back = client.get("/v1/env/AUTH0_DOMAIN", headers=headers)
    assert read_back.status_code == 200
    assert read_back.json() == {"value": "provisioned-on-host.us.auth0.com"}

    # DB authority DSNs are NOT vendable over /v1/env: each runtime plane gets its own
    # least-privilege DSN locally, and the shared transport token is not DB authority.
    assert client.get("/v1/env/DATABASE_URL", headers=headers).status_code == 404

    # And WRITING env over HTTP is refused — no runtime plane provisions secrets this way, which
    # closes the env clobber/DoS vector. The stored value is unchanged.
    saved = client.post("/v1/env/AUTH0_DOMAIN", json={"value": "attacker.us.auth0.com"}, headers=headers)
    assert saved.status_code == 403
    assert client.get("/v1/env/AUTH0_DOMAIN", headers=headers).json() == {"value": "provisioned-on-host.us.auth0.com"}


def test_v1_env_routes_refuse_provider_keys_but_serve_infra(tmp_path, monkeypatch):
    """GOAL_RULES §1 step 4: the /v1/env HTTP routes must REFUSE to vend any paid-provider key (a
    runtime plane must call the safebox broker instead), while still serving infra secrets. The
    safebox's OWN local resolution (for the proxy/broker) is unaffected — covered by the
    read_env_backed_value unit tests above."""
    from plugins.takyon import core

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    headers = {"Authorization": "Bearer shared-token"}

    client = TestClient(build_safebox_app())

    # Every denied provider key 404s over HTTP (no value), even though it is resolvable locally.
    for provider_key in ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                         "OPENAI_API_KEY", "TAVILY_API_KEY", "GEMINI_API_KEY",
                         "TAKYON_GEMINI_API_KEY", "FAL_KEY", "COMPOSIO_API_KEY"):
        monkeypatch.setenv(provider_key, "leaked-secret-should-not-vend")
        resp = client.get(f"/v1/env/{provider_key}", headers=headers)
        assert resp.status_code == 404, provider_key
        assert "leaked-secret-should-not-vend" not in resp.text

    # An ALLOWLISTED infra config name still serves. DB authority DSNs do NOT (each plane gets its
    # own least-privilege DSN locally — the transport token is not DB authority).
    monkeypatch.setenv("POSTMARK_FROM_EMAIL", "hello@infra.example")
    infra = client.get("/v1/env/POSTMARK_FROM_EMAIL", headers=headers)
    assert infra.status_code == 200
    assert infra.json() == {"value": "hello@infra.example"}
    monkeypatch.setenv("DATABASE_URL", "postgres://never-vends")
    dsn = client.get("/v1/env/DATABASE_URL", headers=headers)
    assert dsn.status_code == 404
    assert "never-vends" not in dsn.text

    # /v1/env/first filters denied aliases out, then resolves the first non-denied value. The denied
    # ANTHROPIC_API_KEY (first in the list) is skipped; the allowlisted infra alias resolves.
    monkeypatch.setenv("SUPABASE_S3_REGION", "us-east-2")
    first_mixed = client.post(
        "/v1/env/first",
        json={"keys": ["ANTHROPIC_API_KEY", "SUPABASE_S3_REGION"]},
        headers=headers,
    )
    assert first_mixed.status_code == 200
    assert first_mixed.json() == {"value": "us-east-2"}

    # first asking ONLY for denied keys refuses.
    first_denied = client.post(
        "/v1/env/first",
        json={"keys": ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]},
        headers=headers,
    )
    assert first_denied.status_code == 404

    # NOTE: GET /v1/env/snapshot is shadowed by the earlier /v1/env/{key} route (a PRE-EXISTING
    # route-ordering bug, key='snapshot'), so it is unreachable as a GET and is not asserted here.
    # The snapshot handler still applies the denylist filter (defense in depth) for if/when that
    # ordering is fixed. The /v1/env name-listing route IS reachable and filters denied names:
    listed = client.get("/v1/env", headers=headers).json()["keys"]
    assert "ANTHROPIC_API_KEY" not in listed
    assert "OPENAI_API_KEY" not in listed

    # The denylist is the single canonical source for provider keys, and the egress gate is the
    # canonical read for what /v1/env may vend: provider keys and DB authority DSNs are refused,
    # allowlisted infra config serves.
    assert "ANTHROPIC_API_KEY" in core.provider_key_denylist()
    assert core.env_egress_allowed("DATABASE_URL") is False
    assert core.env_egress_allowed("POSTMARK_FROM_EMAIL") is True


def test_safebox_app_requires_internal_token_and_reads_creative_credit_balance(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    # /v1/creative-credits/* routes are operator-authority on top of the shared transport token.
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_TOKEN", "operator-route-token")
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_CLIENTS", "testclient")
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

    headers = {
        "Authorization": "Bearer shared-token",
        "X-Takyon-Operator-Token": "operator-route-token",
    }
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
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_TOKEN", "operator-route-token")
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_CLIENTS", "testclient")
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

    headers = {
        "Authorization": "Bearer shared-token",
        "X-Takyon-Operator-Token": "operator-route-token",
    }
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


def test_safebox_app_refuses_arbitrary_creative_credit_grant(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_TOKEN", "operator-route-token")
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_CLIENTS", "testclient")

    client = TestClient(build_safebox_app())
    # Even a FULLY authorized operator client cannot mint credits by fiat — grants must derive from a
    # verified checkout/webhook.
    response = client.post(
        "/v1/creative-credits/grant",
        headers={
            "Authorization": "Bearer shared-token",
            "X-Takyon-Operator-Token": "operator-route-token",
        },
        json={
            "business_slug": "acme",
            "credits": 999999,
            "idempotency_key": "attacker",
            "metadata": {},
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "creative_credit_grant_requires_verified_checkout_or_webhook"


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


def test_safebox_app_verifies_app_webhook_signature(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_app_xyz")

    client = TestClient(build_safebox_app())
    body = json.dumps({"id": "evt_app_1", "type": "checkout.session.completed"})
    headers = {"Authorization": "Bearer shared-token"}

    unauthorized = client.post(
        "/v1/stripe/app-webhook/verify",
        json={"raw_body": body, "signature": build_signature_header(body, "whsec_app_xyz")},
    )
    assert unauthorized.status_code == 401

    response = client.post(
        "/v1/stripe/app-webhook/verify",
        headers=headers,
        json={"raw_body": body, "signature": build_signature_header(body, "whsec_app_xyz")},
    )
    assert response.status_code == 200
    assert response.json() == {
        "event": {"id": "evt_app_1", "type": "checkout.session.completed"}
    }


def test_safebox_app_processes_app_webhook_after_signature_verify(monkeypatch):
    from plugins.takyon import app_payments

    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_app_process")

    calls = []

    class _Conn:
        def execute(self, *a, **k):
            # Tolerates the session-config set_config call and the role probe alike.
            return self

        def fetchone(self):
            # The webhook path asserts its connection presents the safebox DB authority role
            # (assert_takyon_pg_role) before any processing — answer the role probe accordingly.
            return {
                "session_user": "takyon_safebox_authority",
                "current_user": "takyon_safebox_authority",
            }

        def close(self):
            pass

    monkeypatch.setattr("psycopg.connect", lambda *a, **k: _Conn())
    monkeypatch.setattr("plugins.takyon.runtime_app.resolve_database_url", lambda *a, **k: "postgres://owner")

    def _fake_process(conn, event):
        calls.append((conn, event))
        return {
            "provider_event_id": event["id"],
            "type": event["type"],
            "deduplicated": False,
            "processed": {"recorded": True},
        }

    monkeypatch.setattr(app_payments, "record_webhook_and_process", _fake_process)

    client = TestClient(build_safebox_app())
    body = json.dumps({"id": "evt_app_process", "type": "checkout.session.completed"})
    headers = {"Authorization": "Bearer shared-token"}

    response = client.post(
        "/v1/stripe/app-webhook/process",
        headers=headers,
        json={"raw_body": body, "signature": build_signature_header(body, "whsec_app_process")},
    )

    assert response.status_code == 200
    assert response.json()["processed"] == {"recorded": True}
    assert calls and calls[0][1]["id"] == "evt_app_process"


def test_safebox_app_webhook_forged_signature_is_400(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_app_xyz")

    client = TestClient(build_safebox_app())
    body = json.dumps({"id": "evt_app_2", "type": "checkout.session.completed"})
    headers = {"Authorization": "Bearer shared-token"}

    forged = build_signature_header(body, "whsec_attacker")
    response = client.post(
        "/v1/stripe/app-webhook/verify",
        headers=headers,
        json={"raw_body": body, "signature": forged},
    )
    assert response.status_code == 400


def test_safebox_app_webhook_unconfigured_is_503(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(safebox, "load_env", lambda: {})

    client = TestClient(build_safebox_app())
    body = json.dumps({"id": "evt_app_3", "type": "checkout.session.completed"})
    headers = {"Authorization": "Bearer shared-token"}

    response = client.post(
        "/v1/stripe/app-webhook/verify",
        headers=headers,
        json={"raw_body": body, "signature": build_signature_header(body, "whsec_app_xyz")},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "app_webhook_unconfigured"


def test_verify_stripe_app_webhook_local_authority(monkeypatch):
    # On the safebox host itself the wrapper reads STRIPE_WEBHOOK_SECRET locally and verifies — no
    # remote POST. Returns the parsed event; never the secret.
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_local")
    assert safebox._use_remote_authority() is False
    body = json.dumps({"id": "evt_local", "type": "customer.subscription.updated"})
    header = build_signature_header(body, "whsec_local")

    event = safebox.verify_stripe_app_webhook(body, header)
    assert event == {"id": "evt_local", "type": "customer.subscription.updated"}


def test_verify_stripe_app_webhook_local_invalid_signature(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_local")
    assert safebox._use_remote_authority() is False
    body = json.dumps({"id": "evt_bad", "type": "checkout.session.completed"})
    forged = build_signature_header(body, "whsec_attacker")
    with pytest.raises(safebox.StripeAppWebhookInvalidSignature):
        safebox.verify_stripe_app_webhook(body, forged)


def test_verify_stripe_app_webhook_local_unconfigured(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(safebox, "load_env", lambda: {})
    assert safebox._use_remote_authority() is False
    assert safebox.read_env_backed_value("STRIPE_WEBHOOK_SECRET") == ""
    body = json.dumps({"id": "evt_x", "type": "checkout.session.completed"})
    header = build_signature_header(body, "whsec_local")
    with pytest.raises(safebox.StripeAppWebhookUnconfigured):
        safebox.verify_stripe_app_webhook(body, header)


def test_verify_stripe_app_webhook_remote_authority_posts_to_route(monkeypatch):
    # A remote-authority (runtime) plane POSTs to /v1/stripe/app-webhook/verify and trusts the parsed
    # event the safebox returns — it never reads STRIPE_WEBHOOK_SECRET itself.
    monkeypatch.delenv("TAKYON_HOST_ROLE", raising=False)
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "https://safebox.test")
    assert safebox._use_remote_authority() is True

    calls: list[tuple[str, str, dict]] = []

    def _fake_remote_json(method, path, payload):
        calls.append((method, path, payload))
        return {"event": {"id": "evt_remote", "type": "checkout.session.completed"}}

    monkeypatch.setattr(safebox, "_remote_json", _fake_remote_json)
    event = safebox.verify_stripe_app_webhook("{}", "t=1,v1=deadbeef")
    assert event == {"id": "evt_remote", "type": "checkout.session.completed"}
    assert calls == [("POST", "/v1/stripe/app-webhook/verify", {"raw_body": "{}", "signature": "t=1,v1=deadbeef"})]


def test_verify_stripe_app_webhook_remote_maps_errors(monkeypatch):
    monkeypatch.delenv("TAKYON_HOST_ROLE", raising=False)
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "https://safebox.test")
    assert safebox._use_remote_authority() is True

    def _raise(status):
        def _inner(method, path, payload):
            raise safebox.RemoteSafeboxError("boom", status_code=status, payload={})
        return _inner

    monkeypatch.setattr(safebox, "_remote_json", _raise(503))
    with pytest.raises(safebox.StripeAppWebhookUnconfigured):
        safebox.verify_stripe_app_webhook("{}", "sig")

    monkeypatch.setattr(safebox, "_remote_json", _raise(400))
    with pytest.raises(safebox.StripeAppWebhookInvalidSignature):
        safebox.verify_stripe_app_webhook("{}", "sig")
