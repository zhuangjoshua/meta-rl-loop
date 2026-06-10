"""Generic product app records rail for saved per-subuser product state.

This leaf owns one concern on the shared product app plane: persist normal
business-defined product records for one sub-user without inventing a second
storage path in browser state, local files, or product-specific tables.

It deliberately stays generic:
  * records are scoped by (business_slug, app_user_id)
  * a record has a normalized ``record_type`` plus opaque ``id``
  * payload truth lives in ``data`` and optional ``metadata``

Auth/session/customer identity still live in ``app_identity``. Billing,
entitlements, and AI usage still live in their existing leaves. This module is
only the durable saved-state substrate that lets product apps close the loop:
input -> result -> save record -> reopen later.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass

from plugins.takyon import app_identity

_MAX_RECORDS_PER_USER = 500
_MAX_RECORD_DATA_BYTES = 262_144
_MAX_RECORD_METADATA_BYTES = 65_536
_MAX_RECORD_TITLE_CHARS = 240
_RECORD_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_RECORD_COLUMNS = (
    "id, business_slug, app_user_id, record_type, title, data, metadata, created_at, updated_at"
)


class AppRecordError(Exception):
    """Base for shared product record errors."""


class AppRecordUserNotFound(AppRecordError):
    """The referenced sub-user could not be resolved in this business."""


class AppRecordNotFound(AppRecordError):
    """The requested record does not exist for this sub-user."""


class AppRecordQuotaExceeded(AppRecordError):
    """The per-subuser durable-record cap was exceeded."""

    def __init__(self, app_user_id: str, limit: int) -> None:
        self.app_user_id = app_user_id
        self.limit = limit
        super().__init__(f"app record limit exceeded for {app_user_id}: max {limit}")


class AppRecordPayloadTooLarge(AppRecordError):
    """One record payload or metadata blob exceeded the rail cap."""

    def __init__(self, field: str, size_bytes: int, limit_bytes: int) -> None:
        self.field = field
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__(f"{field} exceeds {limit_bytes} bytes ({size_bytes})")


@dataclass(frozen=True)
class AppRecord:
    id: str
    business_slug: str
    app_user_id: str
    record_type: str
    title: str | None
    data: object
    metadata: dict
    created_at: object
    updated_at: object


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _payload_size_guard(value, *, field: str, limit_bytes: int) -> object:
    try:
        encoded = _json_dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be JSON-serializable") from exc
    size_bytes = len(encoded.encode("utf-8"))
    if size_bytes > limit_bytes:
        raise AppRecordPayloadTooLarge(field, size_bytes, limit_bytes)
    return value


def _normalize_record_type(value: str) -> str:
    record_type = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    if not _RECORD_TYPE_RE.match(record_type):
        raise ValueError("record_type must match ^[a-z0-9][a-z0-9_]{0,63}$")
    return record_type


def _normalize_record_id(value: str | None) -> str:
    if value is None:
        return uuid.uuid4().hex
    record_id = str(value or "").strip()
    if not record_id:
        raise ValueError("record_id must be non-empty when provided")
    if len(record_id) > 128:
        raise ValueError("record_id must be <= 128 characters")
    return record_id


def _normalize_title(value: str | None) -> str | None:
    if value is None:
        return None
    title = str(value).strip()
    if not title:
        return None
    if len(title) > _MAX_RECORD_TITLE_CHARS:
        raise ValueError(f"title must be <= {_MAX_RECORD_TITLE_CHARS} characters")
    return title


def _normalize_metadata(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("metadata must be an object")
    _payload_size_guard(value, field="metadata", limit_bytes=_MAX_RECORD_METADATA_BYTES)
    return value


def _normalize_data(value):
    if value is None:
        raise ValueError("data is required")
    return _payload_size_guard(value, field="data", limit_bytes=_MAX_RECORD_DATA_BYTES)


def _record_from_row(row) -> AppRecord:
    return AppRecord(
        id=str(row[0]),
        business_slug=str(row[1]),
        app_user_id=str(row[2]),
        record_type=str(row[3]),
        title=None if row[4] is None else str(row[4]),
        data=row[5],
        metadata=row[6] if isinstance(row[6], dict) else {},
        created_at=row[7],
        updated_at=row[8],
    )


def _resolve_existing_user(
    conn,
    business_slug: str,
    *,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
) -> app_identity.AppUser | None:
    if session_token is not None:
        return app_identity.validate_session(conn, business_slug, session_token)
    if app_user_id is not None:
        return app_identity.get_app_user(conn, business_slug, app_user_id=app_user_id)
    if email is not None:
        return app_identity.get_app_user(conn, business_slug, email=email)
    raise ValueError("record lookup requires app_user_id, email, or session_token")


def list_records(
    conn,
    business_slug: str,
    *,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
    record_type: str | None = None,
    limit: int = 50,
) -> tuple[app_identity.AppUser, list[AppRecord]] | None:
    user = _resolve_existing_user(
        conn,
        business_slug,
        app_user_id=app_user_id,
        email=email,
        session_token=session_token,
    )
    if user is None:
        return None
    if not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    limit_value = max(1, min(limit, 200))
    params: list[object] = [business_slug, user.id]
    query = (
        f"select {_RECORD_COLUMNS} from app_records "
        "where business_slug = %s and app_user_id = %s"
    )
    if record_type is not None:
        normalized_type = _normalize_record_type(record_type)
        query += " and record_type = %s"
        params.append(normalized_type)
    query += " order by updated_at desc limit %s"
    params.append(limit_value)
    rows = conn.execute(query, tuple(params)).fetchall()
    return user, [_record_from_row(row) for row in rows]


def get_record(
    conn,
    business_slug: str,
    *,
    record_type: str,
    record_id: str,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
) -> tuple[app_identity.AppUser, AppRecord] | None:
    user = _resolve_existing_user(
        conn,
        business_slug,
        app_user_id=app_user_id,
        email=email,
        session_token=session_token,
    )
    if user is None:
        return None
    row = conn.execute(
        f"select {_RECORD_COLUMNS} from app_records "
        "where business_slug = %s and app_user_id = %s and record_type = %s and id = %s",
        (business_slug, user.id, _normalize_record_type(record_type), _normalize_record_id(record_id)),
    ).fetchone()
    if row is None:
        return None
    return user, _record_from_row(row)


def save_record(
    conn,
    business_slug: str,
    *,
    record_type: str,
    data,
    record_id: str | None = None,
    title: str | None = None,
    metadata: dict | None = None,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
) -> tuple[app_identity.AppUser, AppRecord]:
    if session_token is not None:
        user = app_identity.validate_session(conn, business_slug, session_token)
    elif app_user_id is not None:
        user = app_identity.get_app_user(conn, business_slug, app_user_id=app_user_id)
    elif email is not None:
        user = app_identity.upsert_app_user(conn, business_slug, email)
    else:
        raise ValueError("record save requires app_user_id, email, or session_token")
    if user is None:
        raise AppRecordUserNotFound("app user not found")

    normalized_type = _normalize_record_type(record_type)
    normalized_id = _normalize_record_id(record_id)
    normalized_title = _normalize_title(title)
    normalized_data = _normalize_data(data)
    normalized_metadata = _normalize_metadata(metadata)

    with conn.transaction():
        existing = conn.execute(
            "select 1 from app_records "
            "where business_slug = %s and app_user_id = %s and record_type = %s and id = %s",
            (business_slug, user.id, normalized_type, normalized_id),
        ).fetchone()
        if existing is None:
            count_row = conn.execute(
                "select count(*) from app_records where business_slug = %s and app_user_id = %s",
                (business_slug, user.id),
            ).fetchone()
            if int(count_row[0] or 0) >= _MAX_RECORDS_PER_USER:
                raise AppRecordQuotaExceeded(user.id, _MAX_RECORDS_PER_USER)
            row = conn.execute(
                "insert into app_records ("
                " id, business_slug, app_user_id, record_type, title, data, metadata"
                ") values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb) "
                f"returning {_RECORD_COLUMNS}",
                (
                    normalized_id,
                    business_slug,
                    user.id,
                    normalized_type,
                    normalized_title,
                    _json_dumps(normalized_data),
                    _json_dumps(normalized_metadata),
                ),
            ).fetchone()
        else:
            row = conn.execute(
                "update app_records set title = %s, data = %s::jsonb, metadata = %s::jsonb, updated_at = now() "
                "where business_slug = %s and app_user_id = %s and record_type = %s and id = %s "
                f"returning {_RECORD_COLUMNS}",
                (
                    normalized_title,
                    _json_dumps(normalized_data),
                    _json_dumps(normalized_metadata),
                    business_slug,
                    user.id,
                    normalized_type,
                    normalized_id,
                ),
            ).fetchone()
    return user, _record_from_row(row)


def delete_record(
    conn,
    business_slug: str,
    *,
    record_type: str,
    record_id: str,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
) -> tuple[app_identity.AppUser, AppRecord]:
    user = _resolve_existing_user(
        conn,
        business_slug,
        app_user_id=app_user_id,
        email=email,
        session_token=session_token,
    )
    if user is None:
        raise AppRecordUserNotFound("app user not found")
    with conn.transaction():
        row = conn.execute(
            "delete from app_records "
            "where business_slug = %s and app_user_id = %s and record_type = %s and id = %s "
            f"returning {_RECORD_COLUMNS}",
            (business_slug, user.id, _normalize_record_type(record_type), _normalize_record_id(record_id)),
        ).fetchone()
    if row is None:
        raise AppRecordNotFound("app record not found")
    return user, _record_from_row(row)
