from __future__ import annotations

import os
import uuid

import pytest

from plugins.takyon import safebox
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
