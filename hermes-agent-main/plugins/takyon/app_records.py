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

import base64
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


# --- records-v2 bounded query (§18) -------------------------------------------------
# One compiler, both dialects. Field names are whitelist-only and NEVER interpolated
# from caller input; values ALWAYS become bound parameters. Keyset pagination follows
# the effective ORDER BY with an opaque, versioned cursor.
QUERY_MAX_FILTERS = 5
QUERY_MAX_SORT = 2
QUERY_MAX_LIMIT = 100
QUERY_DEFAULT_LIMIT = 25
_QUERY_IN_MAX = 20
_QUERY_CURSOR_VERSION = 1
_QUERY_COMPARE_OPS = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
_QUERY_OPS = frozenset(set(_QUERY_COMPARE_OPS) | {"in", "ilike", "exists"})
_QUERY_REAL_COLUMNS = frozenset({"record_type", "title", "created_at", "updated_at"})
_QUERY_SORT_COLUMNS = frozenset({"created_at", "updated_at", "title", "record_type"})
_QUERY_DATA_KEY_RE = re.compile(r"^[a-z0-9_]{1,64}$")


class RecordQueryError(AppRecordError):
    """Raised when a records-v2 query is malformed (caller error, surfaced truthfully)."""


@dataclass(frozen=True)
class _Dialect:
    placeholder: str  # "%s" (pg) or "?" (sqlite)
    data_column: str  # "data" (pg jsonb) or "data_json" (sqlite text)

    def json_text(self, key: str) -> str:
        if self.data_column == "data":
            return f"data->>'{key}'"
        return f"json_extract(data_json, '$.{key}')"

    def numeric(self, expr: str) -> str:
        if self.data_column == "data":
            return f"({expr})::numeric"
        return f"CAST({expr} AS REAL)"


_PG_DIALECT = _Dialect("%s", "data")
_SQLITE_DIALECT = _Dialect("?", "data_json")


@dataclass(frozen=True)
class _OrderTerm:
    field: str
    direction: str
    expr: str


def _resolve_query_field(field: str, dialect: _Dialect) -> str:
    """Map a whitelisted field name to a SAFE SQL expression (never raw interpolation)."""
    name = str(field or "").strip()
    if name in _QUERY_REAL_COLUMNS:
        return name  # bare column, whitelist-guaranteed identifier
    if name.startswith("data."):
        key = name[len("data."):]
        if not _QUERY_DATA_KEY_RE.match(key):
            raise RecordQueryError(f"query field `{name}` has an invalid data key (a-z, 0-9, _, ≤64)")
        return dialect.json_text(key)  # key is regex-constrained, never arbitrary text
    raise RecordQueryError(
        f"query field `{name}` is not allowed; use record_type/title/created_at/updated_at or data.<key>"
    )


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _sort_expr(field: str, dialect: _Dialect) -> str:
    if field == "title":
        return "coalesce(title, '')"
    if field in _QUERY_SORT_COLUMNS or field == "id":
        return field
    raise RecordQueryError(f"sort field `{field}` is not allowed")


def _normalize_query_order(sort: object, dialect: _Dialect) -> list[_OrderTerm]:
    raw_sort = sort if isinstance(sort, list) else []
    if len(raw_sort) > QUERY_MAX_SORT:
        raise RecordQueryError(f"query allows at most {QUERY_MAX_SORT} sort keys")
    order_terms: list[_OrderTerm] = []
    seen: set[str] = set()
    for item in raw_sort:
        if not isinstance(item, dict):
            raise RecordQueryError("each sort entry must be an object {field, dir}")
        field = str(item.get("field") or "").strip()
        if field not in _QUERY_SORT_COLUMNS:
            raise RecordQueryError(f"sort field `{field}` is not allowed (created_at,updated_at,title,record_type)")
        if field in seen:
            raise RecordQueryError(f"sort field `{field}` may appear at most once")
        seen.add(field)
        direction = "ASC" if str(item.get("dir") or "desc").strip().lower() == "asc" else "DESC"
        order_terms.append(_OrderTerm(field=field, direction=direction, expr=_sort_expr(field, dialect)))
    if not order_terms:
        order_terms.append(_OrderTerm(field="updated_at", direction="DESC", expr=_sort_expr("updated_at", dialect)))
    order_terms.append(_OrderTerm(field="id", direction=order_terms[-1].direction, expr="id"))
    return order_terms


def _cursor_signature(order_terms: list[_OrderTerm]) -> list[str]:
    return [f"{term.field}:{term.direction.lower()}" for term in order_terms]


def _cursor_field_value(field: str, value: object) -> object:
    if field == "title":
        return "" if value is None else str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _record_cursor_value(record: object, field: str) -> object:
    if isinstance(record, dict):
        return _cursor_field_value(field, record.get(field))
    return _cursor_field_value(field, getattr(record, field))


def _encode_cursor_payload(signature: list[str], values: list[object]) -> str:
    raw = json.dumps(
        {"v": _QUERY_CURSOR_VERSION, "s": signature, "k": values},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def encode_query_record_cursor(record: object, *, sort: object, dialect: _Dialect) -> str:
    order_terms = _normalize_query_order(sort, dialect)
    return _encode_cursor_payload(
        _cursor_signature(order_terms),
        [_record_cursor_value(record, term.field) for term in order_terms],
    )


def encode_record_cursor(updated_at: object, record_id: object) -> str:
    return _encode_cursor_payload(
        ["updated_at:desc", "id:desc"],
        [
            _cursor_field_value("updated_at", updated_at),
            _cursor_field_value("id", record_id),
        ],
    )


def _decode_cursor_payload(cursor: object) -> dict[str, object] | None:
    text = str(cursor or "").strip()
    if not text:
        return None
    try:
        raw = base64.urlsafe_b64decode(text.encode("ascii")).decode("utf-8")
    except Exception as exc:  # malformed cursor is a caller error, not a crash
        raise RecordQueryError("query cursor is malformed") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        updated_at, record_id = raw.rsplit(" ", 1) if " " in raw else ("", "")
        if not updated_at or not record_id:
            raise RecordQueryError("query cursor is malformed")
        return {"s": ["updated_at:desc", "id:desc"], "k": [updated_at, record_id]}
    if not isinstance(payload, dict):
        raise RecordQueryError("query cursor is malformed")
    signature = payload.get("s")
    values = payload.get("k")
    if payload.get("v") != _QUERY_CURSOR_VERSION or not isinstance(signature, list) or not isinstance(values, list):
        raise RecordQueryError("query cursor is malformed")
    if not all(isinstance(item, str) for item in signature):
        raise RecordQueryError("query cursor is malformed")
    return {"s": signature, "k": values}


def _decode_query_cursor(cursor: object, order_terms: list[_OrderTerm]) -> list[object] | None:
    payload = _decode_cursor_payload(cursor)
    if payload is None:
        return None
    signature = payload["s"]
    values = payload["k"]
    expected = _cursor_signature(order_terms)
    if signature != expected:
        raise RecordQueryError("query cursor is for a different sort order")
    if len(values) != len(expected):
        raise RecordQueryError("query cursor is malformed")
    return list(values)


def decode_record_cursor(cursor: object) -> tuple[str, str] | None:
    values = _decode_query_cursor(
        cursor,
        [
            _OrderTerm("updated_at", "DESC", "updated_at"),
            _OrderTerm("id", "DESC", "id"),
        ],
    )
    if values is None:
        return None
    return (str(values[0]), str(values[1]))


def _cursor_where_clause(order_terms: list[_OrderTerm], cursor_values: list[object], placeholder: str) -> tuple[str, list[object]]:
    disjuncts: list[str] = []
    params: list[object] = []
    for index, term in enumerate(order_terms):
        branch: list[str] = []
        for prev_term, prev_value in zip(order_terms[:index], cursor_values[:index]):
            branch.append(f"{prev_term.expr} = {placeholder}")
            params.append(prev_value)
        comparator = ">" if term.direction == "ASC" else "<"
        branch.append(f"{term.expr} {comparator} {placeholder}")
        params.append(cursor_values[index])
        disjuncts.append("(" + " AND ".join(branch) + ")")
    return "(" + " OR ".join(disjuncts) + ")", params


def compile_record_query(
    *,
    filters: object,
    sort: object,
    cursor: object,
    limit: object,
    dialect: _Dialect,
) -> tuple[list[str], str, int, list[object]]:
    """Return (where_fragments, order_sql, limit, params) for a bounded record query."""
    ph = dialect.placeholder
    where: list[str] = []
    params: list[object] = []

    raw_filters = filters if isinstance(filters, list) else []
    if len(raw_filters) > QUERY_MAX_FILTERS:
        raise RecordQueryError(f"query allows at most {QUERY_MAX_FILTERS} filters")
    for item in raw_filters:
        if not isinstance(item, dict):
            raise RecordQueryError("each filter must be an object {field, op, value}")
        op = str(item.get("op") or "").strip().lower()
        if op not in _QUERY_OPS:
            raise RecordQueryError(f"query op `{op}` is not allowed (eq,neq,gt,gte,lt,lte,in,ilike,exists)")
        expr = _resolve_query_field(item.get("field"), dialect)
        value = item.get("value")
        if op in _QUERY_COMPARE_OPS:
            sql_op = _QUERY_COMPARE_OPS[op]
            if op in {"gt", "gte", "lt", "lte"} and _is_number(value):
                where.append(f"{dialect.numeric(expr)} {sql_op} {ph}")
            else:
                where.append(f"{expr} {sql_op} {ph}")
            params.append(value)
        elif op == "in":
            if not isinstance(value, list) or not value:
                raise RecordQueryError("`in` requires a non-empty list value")
            if len(value) > _QUERY_IN_MAX:
                raise RecordQueryError(f"`in` allows at most {_QUERY_IN_MAX} values")
            where.append(f"{expr} IN ({', '.join([ph] * len(value))})")
            params.extend(value)
        elif op == "ilike":
            if dialect.data_column == "data":
                where.append(f"{expr} ILIKE {ph}")
            else:
                where.append(f"{expr} LIKE {ph} COLLATE NOCASE")
            params.append(f"%{value}%")
        elif op == "exists":
            wants = bool(value) if value is not None else True
            where.append(f"{expr} IS NOT NULL" if wants else f"{expr} IS NULL")

    order_terms = _normalize_query_order(sort, dialect)
    order_sql = "ORDER BY " + ", ".join(f"{term.expr} {term.direction}" for term in order_terms)

    decoded = _decode_query_cursor(cursor, order_terms)
    if decoded is not None:
        cursor_sql, cursor_params = _cursor_where_clause(order_terms, decoded, ph)
        where.append(cursor_sql)
        params.extend(cursor_params)

    try:
        limit_value = int(limit) if limit is not None else QUERY_DEFAULT_LIMIT
    except (TypeError, ValueError):
        limit_value = QUERY_DEFAULT_LIMIT
    limit_value = max(1, min(limit_value, QUERY_MAX_LIMIT))
    return where, order_sql, limit_value, params


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


def query_records(
    conn,
    business_slug: str,
    *,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
    record_type: str | None = None,
    filters: object = None,
    sort: object = None,
    cursor: object = None,
    limit: object = None,
) -> tuple[app_identity.AppUser, list[AppRecord], str | None] | None:
    """records-v2 bounded query (PG). Returns (user, records, next_cursor)."""
    user = _resolve_existing_user(
        conn,
        business_slug,
        app_user_id=app_user_id,
        email=email,
        session_token=session_token,
    )
    if user is None:
        return None
    where, order_sql, limit_value, params = compile_record_query(
        filters=filters, sort=sort, cursor=cursor, limit=limit, dialect=_PG_DIALECT
    )
    sql_params: list[object] = [business_slug, user.id]
    query = (
        f"select {_RECORD_COLUMNS} from app_records "
        "where business_slug = %s and app_user_id = %s"
    )
    if record_type is not None:
        query += " and record_type = %s"
        sql_params.append(_normalize_record_type(record_type))
    for fragment in where:
        query += f" and {fragment}"
    sql_params.extend(params)
    # Fetch one extra row to know whether a next page exists.
    query += f" {order_sql} limit %s"
    sql_params.append(limit_value + 1)
    rows = conn.execute(query, tuple(sql_params)).fetchall()
    records = [_record_from_row(row) for row in rows]
    next_cursor: str | None = None
    if len(records) > limit_value:
        records = records[:limit_value]
        last = records[-1]
        next_cursor = encode_query_record_cursor(last, sort=sort, dialect=_PG_DIALECT)
    return user, records, next_cursor


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
