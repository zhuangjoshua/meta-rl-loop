"""Hermetic tests for the product media rail leaf (§19, SQLite path)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins.takyon import app_media
from plugins.takyon.core import _hash_token, _now


class _MemBackend:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}

    def put(self, key, data, *, digest):
        self.blobs[key] = data

    def get(self, key):
        if key not in self.blobs:
            raise KeyError(key)
        return self.blobs[key]

    def delete(self, key):
        self.blobs.pop(key, None)


class _SQLiteStore:
    def __init__(self, conn: sqlite3.Connection, root: Path):
        self._conn = conn
        self._root = root
        self._backend = _MemBackend()

    @contextmanager
    def _connect(self):
        yield self._conn

    def _business_root(self, business: str) -> Path:
        return self._root / business

    def _workspace_storage_backend(self):
        return self._backend

    @staticmethod
    def _row_to_dict(row):
        return dict(row) if row is not None else None

    @staticmethod
    def _ensure_app_budget(conn, business):
        return {"current_period_start": "1970-01-01T00:00:00", "hard_limit_microusd": 50_000_000}


PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


def _store(tmp_path: Path) -> _SQLiteStore:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE app_users (id TEXT PRIMARY KEY, business_slug TEXT, email TEXT, status TEXT DEFAULT 'active', "
        "tier TEXT DEFAULT 'free', created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE app_media (id TEXT PRIMARY KEY, business_slug TEXT, app_user_id TEXT, media_id TEXT, "
        "filename TEXT, mime TEXT, size_bytes INTEGER, storage_key TEXT, created_at TEXT, UNIQUE(business_slug, media_id))"
    )
    conn.execute(
        "CREATE TABLE app_usage_events (id TEXT PRIMARY KEY, business_slug TEXT, app_user_id TEXT, app_user_tier TEXT, "
        "reservation_key TEXT, purpose TEXT, route TEXT, status TEXT, estimated_cost_microusd INTEGER DEFAULT 0, "
        "actual_cost_microusd INTEGER DEFAULT 0, input_tokens INTEGER, output_tokens INTEGER, provider_request_id TEXT, "
        "provider TEXT, model TEXT, metadata_json TEXT, error TEXT, created_at TEXT, completed_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE app_sessions (id TEXT, business_slug TEXT, app_user_id TEXT, token_hash TEXT, "
        "expires_at TEXT, revoked_at TEXT, created_at TEXT)"
    )
    now = _now()
    conn.execute("INSERT INTO app_users (id, business_slug, email, created_at, updated_at) VALUES ('u1','biz','a@example.com',?,?)", (now, now))
    conn.execute("INSERT INTO app_users (id, business_slug, email, created_at, updated_at) VALUES ('u2','biz','b@example.com',?,?)", (now, now))
    conn.execute("INSERT INTO app_users (id, business_slug, email, status, tier, created_at, updated_at) "
                 "VALUES ('svc','biz','scheduler@service.biz.takyon.invalid','active','service',?,?)", (now, now))
    conn.commit()
    return _SQLiteStore(conn, tmp_path)


def _upload(store, **kw):
    base = dict(
        business_slug="biz", app_user_id="u1", filename="pic.png", content=PNG, mime="image/png",
        idempotency_key="m-1", test_mode=False, principal={"kind": "session"},
    )
    base.update(kw)
    return app_media.store_media(store, **base)


def test_store_succeeds_with_receipt_row_and_metered_event(tmp_path):
    store = _store(tmp_path)
    result = _upload(store)
    assert result["mime"] == "image/png" and result["size_bytes"] == len(PNG)
    # blob stored
    assert store._backend.blobs[f"media/biz/{result['media_id']}"] == PNG
    # row + receipt + usage event
    assert store._conn.execute("SELECT COUNT(*) FROM app_media").fetchone()[0] == 1
    assert (tmp_path / "biz" / result["receipt_path"]).is_file()
    ev = store._conn.execute("SELECT purpose, route, status FROM app_usage_events").fetchone()
    assert ev["purpose"] == "media_store" and ev["route"] == "media" and ev["status"] == "completed"


def test_test_mode_suppresses_backend_write(tmp_path):
    store = _store(tmp_path)
    result = _upload(store, test_mode=True)
    assert result["suppressed"] is True
    assert store._backend.blobs == {}  # no bytes written
    assert store._conn.execute("SELECT COUNT(*) FROM app_media").fetchone()[0] == 1  # row still recorded


def test_oversize_rejected(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setenv("TAKYON_APP_MEDIA_MAX_BYTES", "100")
    with pytest.raises(app_media.MediaQuotaExceeded, match="exceeds the 100 byte limit"):
        _upload(store, content=b"y" * 200)


def test_bad_mime_rejected(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(app_media.AppMediaError, match="unsupported media type"):
        _upload(store, mime="application/pdf")


def test_per_user_quota_enforced(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setenv("TAKYON_APP_MEDIA_USER_QUOTA_BYTES", str(len(PNG) + 10))
    _upload(store, idempotency_key="m-a")
    with pytest.raises(app_media.MediaQuotaExceeded, match="per-user media quota exceeded"):
        _upload(store, idempotency_key="m-b")


def test_per_business_quota_enforced(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setenv("TAKYON_APP_MEDIA_USER_QUOTA_BYTES", str(10 * len(PNG)))
    monkeypatch.setenv("TAKYON_APP_MEDIA_BUSINESS_QUOTA_BYTES", str(len(PNG) + 10))
    _upload(store, idempotency_key="m-a")
    with pytest.raises(app_media.MediaQuotaExceeded, match="per-business media quota exceeded"):
        _upload(store, app_user_id="u2", idempotency_key="m-b")


def test_service_identity_upload_rejected(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(app_media.AppMediaError, match="service identities cannot upload"):
        _upload(store, app_user_id="svc")


def test_uploader_only_delete(tmp_path):
    store = _store(tmp_path)
    result = _upload(store)
    with pytest.raises(app_media.AppMediaError, match="only the uploader"):
        app_media.delete_media(store, business_slug="biz", media_id=result["media_id"], app_user_id="u2")
    out = app_media.delete_media(store, business_slug="biz", media_id=result["media_id"], app_user_id="u1")
    assert out["deleted"] is True
    assert store._conn.execute("SELECT COUNT(*) FROM app_media").fetchone()[0] == 0
    assert store._backend.blobs == {}


def test_get_media_returns_bytes_and_mime(tmp_path):
    store = _store(tmp_path)
    result = _upload(store)
    now = _now()
    store._conn.execute(
        "INSERT INTO app_sessions (id, business_slug, app_user_id, token_hash, expires_at, revoked_at, created_at) "
        "VALUES ('s1', 'biz', 'u1', ?, ?, NULL, ?)",
        (_hash_token("tok-u1"), "2099-01-01T00:00:00+00:00", now),
    )
    store._conn.commit()

    fetched = app_media.get_media(store, business_slug="biz", media_id=result["media_id"], session_token="tok-u1")

    assert fetched["mime"] == "image/png"
    assert fetched["content"] == PNG


def test_get_media_rejects_other_users_session(tmp_path):
    store = _store(tmp_path)
    result = _upload(store)
    now = _now()
    store._conn.execute(
        "INSERT INTO app_sessions (id, business_slug, app_user_id, token_hash, expires_at, revoked_at, created_at) "
        "VALUES ('s2', 'biz', 'u2', ?, ?, NULL, ?)",
        (_hash_token("tok-u2"), "2099-01-01T00:00:00+00:00", now),
    )
    store._conn.commit()

    with pytest.raises(app_media.AppMediaError, match="media not found"):
        app_media.get_media(store, business_slug="biz", media_id=result["media_id"], session_token="tok-u2")


def test_media_usage_reports_total(tmp_path):
    store = _store(tmp_path)
    _upload(store, idempotency_key="m-a")
    usage = app_media.media_usage(store, "biz")
    assert usage["count"] == 1 and usage["total_bytes"] == len(PNG)
    assert usage["business_quota_bytes"] == app_media._business_quota()
