from __future__ import annotations

import os
import uuid

import pytest
from starlette.testclient import TestClient

from plugins.takyon import safebox
from plugins.takyon.safebox_app import build_safebox_app
from plugins.takyon.user_api_keys import generate_api_key


def test_read_env_backed_value_prefers_process_env(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_process")
    monkeypatch.setattr(
        safebox,
        "load_env",
        lambda: {"STRIPE_SECRET_KEY": "sk_disk_fallback"},
    )

    assert safebox.read_env_backed_value("STRIPE_SECRET_KEY") == "sk_live_process"


def test_read_env_backed_value_falls_back_to_takyon_env(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setattr(
        safebox,
        "load_env",
        lambda: {"STRIPE_SECRET_KEY": "sk_disk_fallback"},
    )

    assert safebox.read_env_backed_value("STRIPE_SECRET_KEY") == "sk_disk_fallback"


def test_first_env_backed_value_returns_first_populated_alias(monkeypatch):
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


def test_read_env_backed_value_allows_sensitive_api_keys(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        safebox,
        "load_env",
        lambda: {"OPENAI_API_KEY": "sk-openai-disk"},
    )

    assert safebox.read_env_backed_value("OPENAI_API_KEY") == "sk-openai-disk"


def test_read_env_backed_value_rejects_non_sensitive_keys():
    with pytest.raises(KeyError, match="non-sensitive env key"):
        safebox.read_env_backed_value("OPENAI_BASE_URL")


def test_save_and_remove_env_backed_value_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
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

    raw = generate_api_key()
    record = safebox.register_user_api_key("user-1", raw, key_id=str(uuid.uuid4()))
    assert safebox.resolve_user_api_key(raw) is not None

    assert safebox.revoke_user_api_key(str(record["id"])) is True
    assert safebox.resolve_user_api_key(raw) is None


def test_user_api_key_registry_blocks_second_active_key_for_one_user(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))

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


def test_safebox_app_requires_internal_token_and_round_trips_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
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
