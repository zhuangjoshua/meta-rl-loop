"""Product media rail: bounded image upload/serve/delete for generated products.

The rail owns identity, quotas, metering, and receipts; the raw bytes go through the
existing ``storage.StorageBackend`` seam (LocalStorageBackend on disk now, the S3/boto3
driver later) keyed ``media/<business>/<media_id>``. No self-hosted object daemon.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any, Mapping

try:
    from . import app_actions as _app_actions
    from . import storage as _storage
except Exception:  # pragma: no cover - import shape depends on caller
    from plugins.takyon import app_actions as _app_actions
    from plugins.takyon import storage as _storage

is_service_email = _app_actions.is_service_email

_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_DEFAULT_MAX_BYTES = 5_242_880  # 5 MB
_DEFAULT_BUSINESS_QUOTA_BYTES = 1_073_741_824  # 1 GB
_DEFAULT_USER_QUOTA_BYTES = 52_428_800  # 50 MB
_DEFAULT_STORE_PRICE_MICROUSD = 200


class AppMediaError(RuntimeError):
    pass


class MediaQuotaExceeded(AppMediaError):
    pass


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    try:
        return max(0, int(raw)) if raw else default
    except ValueError:
        return default


def _max_bytes() -> int:
    return _env_int("TAKYON_APP_MEDIA_MAX_BYTES", _DEFAULT_MAX_BYTES)


def _business_quota() -> int:
    return _env_int("TAKYON_APP_MEDIA_BUSINESS_QUOTA_BYTES", _DEFAULT_BUSINESS_QUOTA_BYTES)


def _user_quota() -> int:
    return _env_int("TAKYON_APP_MEDIA_USER_QUOTA_BYTES", _DEFAULT_USER_QUOTA_BYTES)


def _store_price() -> int:
    return _env_int("TAKYON_APP_MEDIA_STORE_PRICE_MICROUSD", _DEFAULT_STORE_PRICE_MICROUSD)


def _now() -> str:
    try:
        from .core import _now as core_now
    except Exception:
        from plugins.takyon.core import _now as core_now
    return str(core_now())


def _backend(store: Any):
    return store._workspace_storage_backend()


def _resolve_uploader(store: Any, business_slug: str, app_user_id: str) -> dict[str, Any]:
    try:
        from .core import _PGConn
    except Exception:
        from plugins.takyon.core import _PGConn

    with store._connect() as conn:
        if isinstance(conn, _PGConn):
            leaves = store._app_leaves()
            with store._leaf_conn(conn) as leaf:
                user = leaves["identity"].get_app_user(leaf, business_slug, app_user_id)
            if user is None or str(getattr(user, "status", "") or "") != "active":
                raise AppMediaError("uploader app user not found")
            return {"id": user.id, "email": user.email, "tier": getattr(user, "tier", "") or ""}
        row = conn.execute(
            "SELECT id, email, tier, status FROM app_users WHERE business_slug = ? AND id = ?",
            (business_slug, app_user_id),
        ).fetchone()
        user = store._row_to_dict(row) if row is not None else None
        if not user or str(user.get("status") or "active") != "active":
            raise AppMediaError("uploader app user not found")
        return {"id": str(user.get("id") or ""), "email": str(user.get("email") or ""), "tier": str(user.get("tier") or "")}


def _usage_bytes(store: Any, business_slug: str, app_user_id: str | None) -> int:
    try:
        from .core import _PGConn
    except Exception:
        from plugins.takyon.core import _PGConn

    with store._connect() as conn:
        if isinstance(conn, _PGConn):
            with store._leaf_conn(conn) as leaf:
                if app_user_id is None:
                    row = leaf.execute(
                        "select coalesce(sum(size_bytes), 0) from app_media where business_slug = %s",
                        (business_slug,),
                    ).fetchone()
                else:
                    row = leaf.execute(
                        "select coalesce(sum(size_bytes), 0) from app_media where business_slug = %s and app_user_id = %s",
                        (business_slug, app_user_id),
                    ).fetchone()
            return int(row[0]) if row else 0
        if app_user_id is None:
            row = conn.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM app_media WHERE business_slug = ?",
                (business_slug,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM app_media WHERE business_slug = ? AND app_user_id = ?",
                (business_slug, app_user_id),
            ).fetchone()
        return int(row[0]) if row else 0


def _insert_media_row(store: Any, *, business_slug: str, app_user_id: str, media_id: str,
                      filename: str, mime: str, size_bytes: int, storage_key: str) -> None:
    try:
        from .core import _PGConn
    except Exception:
        from plugins.takyon.core import _PGConn

    now = _now()
    with store._connect() as conn:
        if isinstance(conn, _PGConn):
            with store._leaf_conn(conn) as leaf:
                leaf.execute(
                    "insert into app_media (id, business_slug, app_user_id, media_id, filename, mime, "
                    "size_bytes, storage_key, created_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (uuid.uuid4().hex, business_slug, app_user_id, media_id, filename, mime, size_bytes, storage_key, now),
                )
        else:
            conn.execute(
                "INSERT INTO app_media (id, business_slug, app_user_id, media_id, filename, mime, "
                "size_bytes, storage_key, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, business_slug, app_user_id, media_id, filename, mime, size_bytes, storage_key, now),
            )
            conn.commit()


def _media_row(store: Any, business_slug: str, media_id: str) -> dict[str, Any] | None:
    try:
        from .core import _PGConn
    except Exception:
        from plugins.takyon.core import _PGConn

    with store._connect() as conn:
        if isinstance(conn, _PGConn):
            with store._leaf_conn(conn) as leaf:
                row = leaf.execute(
                    "select app_user_id, mime, size_bytes, storage_key from app_media "
                    "where business_slug = %s and media_id = %s",
                    (business_slug, media_id),
                ).fetchone()
            if row is None:
                return None
            return {"app_user_id": row[0], "mime": row[1], "size_bytes": row[2], "storage_key": row[3]}
        row = conn.execute(
            "SELECT app_user_id, mime, size_bytes, storage_key FROM app_media "
            "WHERE business_slug = ? AND media_id = ?",
            (business_slug, media_id),
        ).fetchone()
        return store._row_to_dict(row) if row is not None else None


def _delete_media_row(store: Any, business_slug: str, media_id: str) -> None:
    try:
        from .core import _PGConn
    except Exception:
        from plugins.takyon.core import _PGConn

    with store._connect() as conn:
        if isinstance(conn, _PGConn):
            with store._leaf_conn(conn) as leaf:
                leaf.execute(
                    "delete from app_media where business_slug = %s and media_id = %s",
                    (business_slug, media_id),
                )
        else:
            conn.execute(
                "DELETE FROM app_media WHERE business_slug = ? AND media_id = ?",
                (business_slug, media_id),
            )
            conn.commit()


def _session_user_id(store: Any, business_slug: str, session_token: str) -> str | None:
    try:
        from .core import _PGConn, _resolve_sqlite_app_user
    except Exception:
        from plugins.takyon.core import _PGConn, _resolve_sqlite_app_user

    with store._connect() as conn:
        if isinstance(conn, _PGConn):
            leaves = store._app_leaves()
            with store._leaf_conn(conn) as leaf:
                user = leaves["identity"].validate_session(leaf, business_slug, session_token)
            return user.id if user is not None else None
        user = _resolve_sqlite_app_user(conn, business_slug, session_token=session_token)
        return str(user.get("id")) if user else None


def media_usage(store: Any, business_slug: str) -> dict[str, Any]:
    try:
        from .core import _PGConn
    except Exception:
        from plugins.takyon.core import _PGConn

    with store._connect() as conn:
        if isinstance(conn, _PGConn):
            with store._leaf_conn(conn) as leaf:
                row = leaf.execute(
                    "select count(*), coalesce(sum(size_bytes), 0) from app_media where business_slug = %s",
                    (business_slug,),
                ).fetchone()
            count, total = (int(row[0]), int(row[1])) if row else (0, 0)
        else:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM app_media WHERE business_slug = ?",
                (business_slug,),
            ).fetchone()
            count, total = (int(row[0]), int(row[1])) if row else (0, 0)
    return {"count": count, "total_bytes": total, "business_quota_bytes": _business_quota()}


def store_media(
    store: Any,
    *,
    business_slug: str,
    app_user_id: str,
    filename: str,
    content: bytes,
    mime: str,
    idempotency_key: str,
    test_mode: bool,
    principal: Mapping[str, Any],
) -> dict[str, Any]:
    mime = str(mime or "").strip().lower()
    if mime not in _ALLOWED_MIME:
        raise AppMediaError(f"unsupported media type {mime!r}; allowed: {', '.join(sorted(_ALLOWED_MIME))}")
    size_bytes = len(content or b"")
    if size_bytes == 0:
        raise AppMediaError("media content is empty")
    if size_bytes > _max_bytes():
        raise MediaQuotaExceeded(f"media exceeds the {_max_bytes()} byte limit ({size_bytes})")

    uploader = _resolve_uploader(store, business_slug, str(app_user_id or "").strip())
    if is_service_email(uploader["email"]):
        raise AppMediaError("service identities cannot upload media")

    user_used = _usage_bytes(store, business_slug, uploader["id"])
    if user_used + size_bytes > _user_quota():
        raise MediaQuotaExceeded(f"per-user media quota exceeded: {user_used + size_bytes}/{_user_quota()} bytes")
    business_used = _usage_bytes(store, business_slug, None)
    if business_used + size_bytes > _business_quota():
        raise MediaQuotaExceeded(f"per-business media quota exceeded: {business_used + size_bytes}/{_business_quota()} bytes")

    price = _store_price()
    metadata = {"recipient": uploader["id"], "principal": str(principal.get("kind") or "session"), "mime": mime}
    _app_actions._reserve_usage(
        store,
        business_slug,
        reservation_key=idempotency_key,
        app_user_id=uploader["id"],
        app_user_tier=uploader["tier"] or None,
        estimate_microusd=price,
        route="media",
        metadata=dict(metadata),
        purpose="media_store",
    )

    media_id = uuid.uuid4().hex
    storage_key = f"media/{business_slug}/{media_id}"
    try:
        if not test_mode:
            digest = hashlib.sha256(content).hexdigest()
            _backend(store).put(storage_key, content, digest=digest)
        _insert_media_row(
            store,
            business_slug=business_slug,
            app_user_id=uploader["id"],
            media_id=media_id,
            filename=str(filename or "")[:200],
            mime=mime,
            size_bytes=size_bytes,
            storage_key=storage_key,
        )
    except Exception as exc:
        _app_actions._release_usage(
            store, business_slug, reservation_key=idempotency_key, error=str(exc)[:300], metadata=dict(metadata)
        )
        raise

    _app_actions._settle_usage(
        store, business_slug, reservation_key=idempotency_key, actual_microusd=price,
        metadata={**metadata, "media_id": media_id, "size_bytes": size_bytes, "suppressed": bool(test_mode)},
    )

    receipt_rel = f"metrics/receipts/app-media/{media_id}.json"
    receipt_abs = store._business_root(business_slug) / receipt_rel
    _app_actions._write_receipt(receipt_abs, {
        "kind": "app_media_store",
        "business": business_slug,
        "media_id": media_id,
        "uploader_app_user_id": uploader["id"],
        "mime": mime,
        "size_bytes": size_bytes,
        "principal": str(principal.get("kind") or "session"),
        "suppressed": bool(test_mode),
        "cost_microusd": price,
        "created_at": _now(),
    })
    return {
        "media_id": media_id,
        "url": f"media/{media_id}",
        "size_bytes": size_bytes,
        "mime": mime,
        "suppressed": bool(test_mode),
        "receipt_path": receipt_rel,
    }


def get_media(store: Any, *, business_slug: str, media_id: str, session_token: str) -> dict[str, Any]:
    if _session_user_id(store, business_slug, str(session_token or "").strip()) is None:
        raise AppMediaError("app account not found")
    row = _media_row(store, business_slug, str(media_id or "").strip())
    if row is None:
        raise AppMediaError("media not found")
    content = _backend(store).get(str(row["storage_key"]))
    return {"content": content, "mime": str(row["mime"]), "size_bytes": int(row["size_bytes"])}


def delete_media(store: Any, *, business_slug: str, media_id: str, app_user_id: str) -> dict[str, Any]:
    row = _media_row(store, business_slug, str(media_id or "").strip())
    if row is None:
        raise AppMediaError("media not found")
    if str(row["app_user_id"]) != str(app_user_id):
        raise AppMediaError("only the uploader can delete this media")
    try:
        _backend(store).delete(str(row["storage_key"]))
    except Exception:
        pass  # row deletion is authoritative; orphaned bytes are swept separately
    _delete_media_row(store, business_slug, str(media_id))
    return {"media_id": str(media_id), "deleted": True}
