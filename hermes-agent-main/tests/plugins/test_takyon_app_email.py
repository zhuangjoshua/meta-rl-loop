"""Hermetic tests for the product email rail leaf (SQLite path)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from plugins.takyon import app_email
from plugins.takyon.core import _now


class _SQLiteStore:
    def __init__(self, conn: sqlite3.Connection, root: Path):
        self._conn = conn
        self._root = root

    @contextmanager
    def _connect(self):
        yield self._conn

    def _business_root(self, business: str) -> Path:
        return self._root / business

    @staticmethod
    def _row_to_dict(row):
        return dict(row) if row is not None else None

    @staticmethod
    def _ensure_app_budget(conn, business):
        return {"current_period_start": "1970-01-01T00:00:00", "hard_limit_microusd": 5_000_000}


def _store(tmp_path: Path) -> _SQLiteStore:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE app_users ("
        "id TEXT PRIMARY KEY, business_slug TEXT NOT NULL, email TEXT NOT NULL, "
        "name TEXT, status TEXT NOT NULL DEFAULT 'active', tier TEXT NOT NULL DEFAULT 'free', "
        "metadata_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE app_usage_events ("
        "id TEXT PRIMARY KEY, business_slug TEXT NOT NULL, app_user_id TEXT, app_user_tier TEXT, "
        "reservation_key TEXT, purpose TEXT NOT NULL, route TEXT NOT NULL, status TEXT NOT NULL, "
        "estimated_cost_microusd INTEGER NOT NULL DEFAULT 0, actual_cost_microusd INTEGER NOT NULL DEFAULT 0, "
        "input_tokens INTEGER, output_tokens INTEGER, provider_request_id TEXT, provider TEXT, model TEXT, "
        "metadata_json TEXT, error TEXT, created_at TEXT NOT NULL, completed_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE app_entitlements ("
        "id TEXT PRIMARY KEY, business_slug TEXT NOT NULL, app_user_id TEXT NOT NULL, "
        "tier TEXT NOT NULL, status TEXT NOT NULL, source TEXT, plan_key TEXT, metadata_json TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    now = _now()
    conn.execute(
        "INSERT INTO app_users (id, business_slug, email, status, tier, created_at, updated_at) "
        "VALUES ('u1', 'biz', 'casey@example.com', 'active', 'pro', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO app_entitlements (id, business_slug, app_user_id, tier, status, source, plan_key, created_at, updated_at) "
        "VALUES ('ent-u1', 'biz', 'u1', 'pro', 'active', 'stripe', 'pro', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO app_users (id, business_slug, email, status, tier, created_at, updated_at) "
        "VALUES ('svc', 'biz', 'scheduler@service.biz.takyon.invalid', 'active', 'service', ?, ?)",
        (now, now),
    )
    conn.commit()
    return _SQLiteStore(conn, tmp_path)


def _send(store, **overrides):
    kwargs = dict(
        business_slug="biz",
        recipient_app_user_id="u1",
        subject="You have a new match",
        text_body="Someone liked you back.",
        purpose="new_match_notice",
        idempotency_key="email-test-1",
        test_mode=True,
        principal={"kind": "service"},
    )
    kwargs.update(overrides)
    return app_email.send_app_email(store, **kwargs)


def _events(store):
    rows = store._conn.execute(
        "SELECT status, purpose, route, actual_cost_microusd FROM app_usage_events"
    ).fetchall()
    return [dict(row) for row in rows]


def test_test_mode_send_is_suppressed_with_receipt_and_settled_event(tmp_path):
    store = _store(tmp_path)

    result = _send(store)

    assert result["suppressed"] is True
    assert result["provider_message_id"].startswith("test-mode-suppressed:")
    receipt = tmp_path / "biz" / result["receipt_path"]
    assert receipt.is_file()
    body = receipt.read_text(encoding="utf-8")
    receipt_data = json.loads(body)
    assert receipt_data["recipient_domain"] == "example.com"
    assert receipt_data["principal"] == "service"
    assert "casey@example.com" not in body
    events = _events(store)
    assert len(events) == 1
    assert events[0]["status"] == "completed"
    assert events[0]["purpose"] == "email_send"
    assert events[0]["route"] == "email"


def test_service_identity_recipient_is_rejected(tmp_path):
    store = _store(tmp_path)

    with pytest.raises(app_email.AppEmailError, match="service identities cannot receive"):
        _send(store, recipient_app_user_id="svc")
    assert _events(store) == []


def test_unknown_recipient_is_rejected(tmp_path):
    store = _store(tmp_path)

    with pytest.raises(app_email.AppEmailError, match="recipient app user not found"):
        _send(store, recipient_app_user_id="nope")


def test_daily_cap_is_enforced(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setenv("TAKYON_APP_EMAIL_DAILY_CAP", "2")

    _send(store, idempotency_key="k1")
    _send(store, idempotency_key="k2")
    with pytest.raises(app_email.EmailDailyCapExceeded, match="daily send cap reached: 2/2"):
        _send(store, idempotency_key="k3")


def test_subject_and_body_validation(tmp_path):
    store = _store(tmp_path)

    with pytest.raises(app_email.AppEmailError, match="subject is required"):
        _send(store, subject="")
    with pytest.raises(app_email.AppEmailError, match="text body is required"):
        _send(store, text_body="")
    with pytest.raises(app_email.AppEmailError, match="purpose must be a lowercase slug"):
        _send(store, purpose="Not A Slug!")


def test_live_mode_missing_creds_fails_truthfully_and_releases(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.delenv("POSTMARK_FROM_EMAIL", raising=False)
    monkeypatch.setattr(
        "plugins.takyon.safebox.read_env_backed_value", lambda name: "", raising=False
    )

    with pytest.raises(app_email.AppEmailError, match="requires POSTMARK_SERVER_TOKEN"):
        _send(store, test_mode=False)
    events = _events(store)
    assert len(events) == 1
    assert events[0]["status"] != "reserved"
    assert events[0]["actual_cost_microusd"] == 0


def test_live_mode_sends_through_provider(tmp_path, monkeypatch):
    store = _store(tmp_path)
    sent = []

    def fake_provider(to_email, subject, text_body, html_body):
        sent.append({"to": to_email, "subject": subject, "html": html_body})
        return "pm-123"

    monkeypatch.setattr(app_email, "_send_postmark", fake_provider)

    result = _send(store, test_mode=False, html_body="<p>hi</p>")

    assert result["suppressed"] is False
    assert result["provider_message_id"] == "pm-123"
    assert sent == [{"to": "casey@example.com", "subject": "You have a new match", "html": "<p>hi</p>"}]
    assert _events(store)[0]["status"] == "completed"


def test_live_mode_uses_safebox_broker_when_enabled(tmp_path, monkeypatch):
    store = _store(tmp_path)
    calls = []

    monkeypatch.setattr("plugins.takyon.safebox.provider_broker_enabled", lambda: True)

    def fake_broker(provider, op, payload, *, estimate_microusd, business, action, session_token, timeout=180.0):
        calls.append(
            {
                "provider": provider,
                "op": op,
                "payload": payload,
                "estimate_microusd": estimate_microusd,
                "business": business,
                "action": action,
                "session_token": session_token,
            }
        )
        return {"message_id": "pm-broker-123", "provider": "postmark", "status": "sent"}

    monkeypatch.setattr("plugins.takyon.safebox.broker_provider_call", fake_broker)

    result = _send(store, test_mode=False, service_session_token="svc-session")

    assert result["brokered"] is True
    assert result["provider_message_id"] == "pm-broker-123"
    assert calls == [
        {
            "provider": "postmark",
            "op": "send",
            "payload": {
                "recipient_app_user_id": "u1",
                "subject": "You have a new match",
                "text_body": "Someone liked you back.",
                "html_body": None,
                "message_stream": None,
            },
            "estimate_microusd": 1500,
            "business": "biz",
            "action": "postmark.send",
            "session_token": "svc-session",
        }
    ]
    assert _events(store) == []


def test_live_service_session_email_requires_broker(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr("plugins.takyon.safebox.provider_broker_enabled", lambda: False)
    monkeypatch.setattr(app_email, "_send_postmark", lambda *a, **k: pytest.fail("legacy postmark called"))

    with pytest.raises(app_email.AppEmailError, match="requires the Safebox provider broker"):
        _send(store, test_mode=False, service_session_token="svc-session")

    assert _events(store) == []
