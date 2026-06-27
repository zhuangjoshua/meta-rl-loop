"""Generic authenticated directory rail for opted-in subuser discoverability.

This leaf owns one concern on the shared product app plane: expose a safe,
consented projection of other subusers in the same business without turning the
private profile rail or the per-user records rail into ad hoc shared state.

It deliberately stays generic:
  * discoverability is authenticated and business-scoped
  * opt-in defaults off
  * the public projection is a consented snapshot stored on the profile row
  * block suppression is enforced against the shared connections table
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from plugins.takyon import app_identity, app_profiles

_MAX_DIRECTORY_PROFILE_BYTES = 16_384
_MAX_DIRECTORY_ATTRIBUTES_BYTES = 8_192
_MAX_DIRECTORY_DISPLAY_NAME_CHARS = 120
_MAX_DIRECTORY_HEADLINE_CHARS = 240
_MAX_DIRECTORY_BIO_CHARS = 4_000
_ENTRY_COLUMNS = (
    "p.id, p.business_slug, p.directory_enabled, p.directory_profile, "
    "p.created_at, coalesce(p.directory_updated_at, p.updated_at) as updated_at"
)
_USER_COLUMNS = "u.id, u.business_slug, u.email, u.name, u.status, u.tier"


class AppDirectoryError(Exception):
    """Base for app directory errors."""


class AppDirectoryUserNotFound(AppDirectoryError):
    """The referenced subuser could not be resolved in this business."""


class AppDirectoryEntryNotFound(AppDirectoryError):
    """The requested directory entry is not visible to the caller."""


@dataclass(frozen=True)
class AppDirectoryEntry:
    app_user_id: str
    business_slug: str
    enabled: bool
    profile: dict
    created_at: object
    updated_at: object


@dataclass(frozen=True)
class ResolvedAppDirectoryEntry:
    user: app_identity.AppUser
    entry: AppDirectoryEntry


def _reject_session_identity_override(
    *,
    session_token: str | None,
    app_user_id: str | None,
    email: str | None,
) -> None:
    if session_token is not None and (app_user_id is not None or email is not None):
        raise ValueError("session_token is authoritative; omit app_user_id/email")


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _payload_size_guard(value, *, field: str, limit_bytes: int) -> object:
    try:
        encoded = _json_dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be JSON-serializable") from exc
    size_bytes = len(encoded.encode("utf-8"))
    if size_bytes > limit_bytes:
        raise ValueError(f"{field} exceeds {limit_bytes} bytes ({size_bytes})")
    return value


def _normalize_text(value, *, field: str, max_chars: int, empty_means_none: bool) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None if empty_means_none else ""
    if len(text) > max_chars:
        raise ValueError(f"{field} must be <= {max_chars} characters")
    return text


def _normalize_attributes(value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("directory attributes must be an object")
    _payload_size_guard(value, field="directory attributes", limit_bytes=_MAX_DIRECTORY_ATTRIBUTES_BYTES)
    return value


def _normalize_profile(
    *,
    existing_profile: dict | None = None,
    display_name=None,
    headline=None,
    bio=None,
    attributes=None,
) -> dict:
    profile = dict(existing_profile or {})
    if display_name is not None:
        normalized = _normalize_text(
            display_name,
            field="directory display_name",
            max_chars=_MAX_DIRECTORY_DISPLAY_NAME_CHARS,
            empty_means_none=True,
        )
        if normalized is None:
            profile.pop("display_name", None)
        else:
            profile["display_name"] = normalized
    if headline is not None:
        normalized = _normalize_text(
            headline,
            field="directory headline",
            max_chars=_MAX_DIRECTORY_HEADLINE_CHARS,
            empty_means_none=True,
        )
        if normalized is None:
            profile.pop("headline", None)
        else:
            profile["headline"] = normalized
    if bio is not None:
        normalized = _normalize_text(
            bio,
            field="directory bio",
            max_chars=_MAX_DIRECTORY_BIO_CHARS,
            empty_means_none=False,
        )
        if normalized in {None, ""}:
            profile.pop("bio", None)
        else:
            profile["bio"] = normalized
    if attributes is not None:
        normalized = _normalize_attributes(attributes)
        if not normalized:
            profile.pop("attributes", None)
        else:
            profile["attributes"] = normalized
    allowlisted = {
        key: value
        for key, value in profile.items()
        if key in {"display_name", "headline", "bio", "attributes"}
    }
    _payload_size_guard(allowlisted, field="directory profile", limit_bytes=_MAX_DIRECTORY_PROFILE_BYTES)
    return allowlisted


def _entry_from_row(row) -> AppDirectoryEntry:
    return AppDirectoryEntry(
        app_user_id=str(row[0]),
        business_slug=str(row[1]),
        enabled=bool(row[2]),
        profile=row[3] if isinstance(row[3], dict) else {},
        created_at=row[4],
        updated_at=row[5],
    )


def _resolved_from_joined_row(row) -> ResolvedAppDirectoryEntry:
    user = app_identity.AppUser(
        id=str(row[0]),
        business_slug=str(row[1]),
        email=str(row[2]),
        name=None if row[3] is None else str(row[3]),
        status=str(row[4]),
        tier=str(row[5]),
    )
    entry = AppDirectoryEntry(
        app_user_id=str(row[6]),
        business_slug=str(row[7]),
        enabled=bool(row[8]),
        profile=row[9] if isinstance(row[9], dict) else {},
        created_at=row[10],
        updated_at=row[11],
    )
    return ResolvedAppDirectoryEntry(user=user, entry=entry)


def _default_entry(user: app_identity.AppUser) -> ResolvedAppDirectoryEntry:
    return ResolvedAppDirectoryEntry(
        user=user,
        entry=AppDirectoryEntry(
            app_user_id=user.id,
            business_slug=user.business_slug,
            enabled=False,
            profile={},
            created_at="",
            updated_at="",
        ),
    )


def _resolve_existing_user(
    conn,
    business_slug: str,
    *,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
) -> app_identity.AppUser | None:
    _reject_session_identity_override(
        session_token=session_token,
        app_user_id=app_user_id,
        email=email,
    )
    if session_token is not None:
        return app_identity.validate_session(conn, business_slug, session_token)
    if app_user_id is not None:
        return app_identity.get_app_user(conn, business_slug, app_user_id=app_user_id)
    if email is not None:
        return app_identity.get_app_user(conn, business_slug, email=email)
    raise ValueError("directory lookup requires app_user_id, email, or session_token")


def _resolve_writable_user(
    conn,
    business_slug: str,
    *,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
) -> app_identity.AppUser:
    _reject_session_identity_override(
        session_token=session_token,
        app_user_id=app_user_id,
        email=email,
    )
    if session_token is not None:
        user = app_identity.validate_session(conn, business_slug, session_token)
    elif app_user_id is not None:
        user = app_identity.get_app_user(conn, business_slug, app_user_id=app_user_id)
    elif email is not None:
        user = app_identity.upsert_app_user(conn, business_slug, email)
    else:
        raise ValueError("directory write requires app_user_id, email, or session_token")
    if user is None:
        raise AppDirectoryUserNotFound("app user not found")
    return user


def get_self_entry(
    conn,
    business_slug: str,
    *,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
) -> ResolvedAppDirectoryEntry | None:
    user = _resolve_existing_user(
        conn,
        business_slug,
        app_user_id=app_user_id,
        email=email,
        session_token=session_token,
    )
    if user is None:
        return None
    ensured = app_profiles.ensure_profile(
        conn,
        business_slug,
        app_user_id=user.id,
        display_name=user.name,
    )
    row = conn.execute(
        f"select {_ENTRY_COLUMNS} from app_user_profiles p "
        "where p.business_slug = %s and p.id = %s",
        (business_slug, ensured.user.id),
    ).fetchone()
    if row is None:
        return _default_entry(ensured.user)
    return ResolvedAppDirectoryEntry(user=ensured.user, entry=_entry_from_row(row))


def upsert_entry(
    conn,
    business_slug: str,
    *,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
    display_name=None,
    headline=None,
    bio=None,
    attributes=None,
) -> ResolvedAppDirectoryEntry:
    user = _resolve_writable_user(
        conn,
        business_slug,
        app_user_id=app_user_id,
        email=email,
        session_token=session_token,
    )
    with conn.transaction():
        app_profiles.ensure_profile(
            conn,
            business_slug,
            app_user_id=user.id,
            display_name=user.name,
        )
        existing_row = conn.execute(
            "select directory_profile from app_user_profiles where business_slug = %s and id = %s",
            (business_slug, user.id),
        ).fetchone()
        existing_profile = (
            existing_row[0]
            if existing_row is not None and isinstance(existing_row[0], dict)
            else {}
        )
        profile = _normalize_profile(
            existing_profile=existing_profile,
            display_name=display_name,
            headline=headline,
            bio=bio,
            attributes=attributes,
        )
        row = conn.execute(
            "update app_user_profiles set "
            " directory_enabled = true, "
            " directory_profile = %s::jsonb, "
            " directory_updated_at = now(), "
            " updated_at = now() "
            "where business_slug = %s and id = %s "
            f"returning {_ENTRY_COLUMNS}",
            (_json_dumps(profile), business_slug, user.id),
        ).fetchone()
    return ResolvedAppDirectoryEntry(user=user, entry=_entry_from_row(row))


def disable_entry(
    conn,
    business_slug: str,
    *,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
) -> ResolvedAppDirectoryEntry:
    user = _resolve_writable_user(
        conn,
        business_slug,
        app_user_id=app_user_id,
        email=email,
        session_token=session_token,
    )
    with conn.transaction():
        app_profiles.ensure_profile(
            conn,
            business_slug,
            app_user_id=user.id,
            display_name=user.name,
        )
        row = conn.execute(
            "update app_user_profiles set "
            " directory_enabled = false, "
            " directory_profile = '{}'::jsonb, "
            " directory_updated_at = now(), "
            " updated_at = now() "
            "where business_slug = %s and id = %s "
            f"returning {_ENTRY_COLUMNS}",
            (business_slug, user.id),
        ).fetchone()
    return ResolvedAppDirectoryEntry(user=user, entry=_entry_from_row(row))


def list_visible_entries(
    conn,
    business_slug: str,
    *,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
    limit: int = 50,
) -> tuple[app_identity.AppUser, list[ResolvedAppDirectoryEntry]] | None:
    viewer = _resolve_existing_user(
        conn,
        business_slug,
        app_user_id=app_user_id,
        email=email,
        session_token=session_token,
    )
    if viewer is None:
        return None
    if not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    limit_value = max(1, min(limit, 100))
    if session_token is not None and app_identity._is_app_runtime_user(conn):
        rows = conn.execute(
            "select * from takyon_app_visible_directory_entries(%s, %s, %s)",
            (business_slug, app_identity._hash_token(session_token), limit_value),
        ).fetchall()
        return viewer, [_resolved_from_joined_row(row) for row in rows]
    rows = conn.execute(
        "select "
        f"{_USER_COLUMNS}, {_ENTRY_COLUMNS} "
        "from app_user_profiles p "
        "join app_users u on u.business_slug = p.business_slug and u.id = p.id "
        "where p.business_slug = %s "
        "  and p.directory_enabled = true "
        "  and u.status = 'active' "
        "  and p.id <> %s "
        "  and not exists ("
        "    select 1 from app_connections c "
        "    where c.business_slug = p.business_slug "
        "      and c.state = 'block' "
        "      and ("
        "        (c.source_app_user_id = %s and c.target_app_user_id = p.id) "
        "        or (c.source_app_user_id = p.id and c.target_app_user_id = %s)"
        "      )"
        "  ) "
        "order by coalesce(p.directory_updated_at, p.updated_at) desc, p.id asc "
        "limit %s",
        (business_slug, viewer.id, viewer.id, viewer.id, limit_value),
    ).fetchall()
    return viewer, [_resolved_from_joined_row(row) for row in rows]


def get_visible_entry(
    conn,
    business_slug: str,
    *,
    target_app_user_id: str = "",
    target_email: str | None = None,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
) -> tuple[app_identity.AppUser, ResolvedAppDirectoryEntry] | None:
    viewer = _resolve_existing_user(
        conn,
        business_slug,
        app_user_id=app_user_id,
        email=email,
        session_token=session_token,
    )
    if viewer is None:
        return None
    if session_token is not None and app_identity._is_app_runtime_user(conn):
        row = conn.execute(
            "select * from takyon_app_visible_directory_entry(%s, %s, %s, %s)",
            (
                business_slug,
                app_identity._hash_token(session_token),
                target_app_user_id,
                target_email,
            ),
        ).fetchone()
        if row is None:
            return None
        return viewer, _resolved_from_joined_row(row)
    row = conn.execute(
        "select "
        f"{_USER_COLUMNS}, {_ENTRY_COLUMNS} "
        "from app_user_profiles p "
        "join app_users u on u.business_slug = p.business_slug and u.id = p.id "
        "where p.business_slug = %s "
        "  and (p.id::text = %s or (%s <> '' and lower(u.email::text) = lower(%s))) "
        "  and p.directory_enabled = true "
        "  and u.status = 'active' "
        "  and p.id <> %s "
        "  and not exists ("
        "    select 1 from app_connections c "
        "    where c.business_slug = p.business_slug "
        "      and c.state = 'block' "
        "      and ("
        "        (c.source_app_user_id = %s and c.target_app_user_id = p.id) "
        "        or (c.source_app_user_id = p.id and c.target_app_user_id = %s)"
        "      )"
        "  ) "
        "limit 1",
        (business_slug, target_app_user_id, str(target_email or "").strip(), str(target_email or "").strip(), viewer.id, viewer.id, viewer.id),
    ).fetchone()
    if row is None:
        return None
    return viewer, _resolved_from_joined_row(row)


def list_admin_entries(
    conn,
    business_slug: str,
    *,
    include_disabled: bool = False,
    limit: int = 50,
) -> list[ResolvedAppDirectoryEntry]:
    if not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    limit_value = max(1, min(limit, 200))
    query = (
        "select "
        f"{_USER_COLUMNS}, "
        "u.id, u.business_slug, "
        "coalesce(p.directory_enabled, false), "
        "coalesce(p.directory_profile, '{}'::jsonb), "
        "coalesce(p.created_at, u.created_at), "
        "coalesce(p.directory_updated_at, p.updated_at, u.updated_at) "
        "from app_users u "
        "left join app_user_profiles p on p.business_slug = u.business_slug and p.id = u.id "
        "where u.business_slug = %s "
    )
    params: list[object] = [business_slug]
    if not include_disabled:
        query += " and coalesce(p.directory_enabled, false) = true "
    query += "order by coalesce(p.directory_updated_at, p.updated_at, u.updated_at) desc, u.id asc limit %s"
    params.append(limit_value)
    rows = conn.execute(query, tuple(params)).fetchall()
    return [_resolved_from_joined_row(row) for row in rows]


def read_admin_entry(
    conn,
    business_slug: str,
    *,
    app_user_id: str | None = None,
    email: str | None = None,
) -> ResolvedAppDirectoryEntry | None:
    user = _resolve_existing_user(
        conn,
        business_slug,
        app_user_id=app_user_id,
        email=email,
    )
    if user is None:
        return None
    row = conn.execute(
        f"select {_ENTRY_COLUMNS} from app_user_profiles p where p.business_slug = %s and p.id = %s",
        (business_slug, user.id),
    ).fetchone()
    if row is None:
        return _default_entry(user)
    return ResolvedAppDirectoryEntry(user=user, entry=_entry_from_row(row))
