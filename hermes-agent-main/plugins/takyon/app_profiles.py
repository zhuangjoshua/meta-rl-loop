"""Generic product sub-user profile rail on the shared app plane.

Builds on ``app_identity`` (the sub-user spine). One concern, scoped by
``business_slug``: persist a business-owned profile record for one sub-user
without creating a second identity system. The profile row is a true 1:1
extension of ``app_users`` and is keyed directly by the sub-user id (same
shape as the common ``profiles.id references users.id`` pattern): auth/
session/customer truth stays in ``app_users``, while mutable product-domain
profile fields live in ``app_user_profiles``.

This is intentionally generic rather than dating-specific. A business can use
it for member bios, creator pages, onboarding answers, or a dating profile
later, while keeping richer domain tables available as a later extension.

House style matches the other Postgres leaves: pure leaf, psycopg connection
passed in, no psycopg import, mutating ops open their own transaction, and
broken preconditions raise typed errors.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from plugins.takyon import app_identity


class AppProfileError(Exception):
    """Base for product profile errors."""


class AppProfileUserNotFound(AppProfileError):
    """The referenced sub-user could not be resolved in this business."""


@dataclass(frozen=True)
class AppProfile:
    """One product profile row for a sub-user. ``id`` is the same value as ``app_users.id``."""

    id: str
    business_slug: str
    display_name: str | None
    headline: str | None
    bio: str
    attributes: dict
    metadata: dict
    created_at: object
    updated_at: object

    @property
    def app_user_id(self) -> str:
        return self.id


@dataclass(frozen=True)
class ResolvedAppProfile:
    """Resolved sub-user plus their optional profile row."""

    user: app_identity.AppUser
    profile: AppProfile | None


_PROFILE_COLUMNS = (
    "id, business_slug, display_name, headline, bio, "
    "attributes, metadata, created_at, updated_at"
)


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _normalize_object_field(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return {"value": value}


def _profile_from_row(row) -> AppProfile:
    return AppProfile(
        id=str(row[0]),
        business_slug=str(row[1]),
        display_name=None if row[2] is None else str(row[2]),
        headline=None if row[3] is None else str(row[3]),
        bio=str(row[4] or ""),
        attributes=row[5] if isinstance(row[5], dict) else {},
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
    raise ValueError("profile lookup requires app_user_id, email, or session_token")


def get_profile(
    conn,
    business_slug: str,
    *,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
) -> ResolvedAppProfile | None:
    """Resolve one sub-user and their optional profile row, or None if the user is absent."""
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
        f"select {_PROFILE_COLUMNS} from app_user_profiles "
        "where business_slug = %s and id = %s",
        (business_slug, user.id),
    ).fetchone()
    return ResolvedAppProfile(
        user=user,
        profile=None if row is None else _profile_from_row(row),
    )


def ensure_profile(
    conn,
    business_slug: str,
    *,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
    display_name: str | None = None,
) -> ResolvedAppProfile:
    """Create the standard 1:1 profile row if absent and return it.

    This mirrors the common starter-kit shape where a user has a profile row from the start,
    rather than lazily materializing the row only on first explicit profile edit.
    """
    user = _resolve_existing_user(
        conn,
        business_slug,
        app_user_id=app_user_id,
        email=email,
        session_token=session_token,
    )
    if user is None:
        raise AppProfileUserNotFound("app user not found")
    with conn.transaction():
        conn.execute(
            "insert into app_user_profiles (id, business_slug, display_name) "
            "values (%s, %s, %s) on conflict (id) do nothing",
            (user.id, business_slug, display_name if display_name is not None else user.name),
        )
        row = conn.execute(
            f"select {_PROFILE_COLUMNS} from app_user_profiles "
            "where business_slug = %s and id = %s",
            (business_slug, user.id),
        ).fetchone()
    return ResolvedAppProfile(user=user, profile=_profile_from_row(row))


def upsert_profile(
    conn,
    business_slug: str,
    *,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
    display_name: str | None = None,
    headline: str | None = None,
    bio: str | None = None,
    attributes: dict | None = None,
    metadata: dict | None = None,
) -> ResolvedAppProfile:
    """Create/update a generic profile row for one sub-user.

    Email may auto-provision the app user through ``app_identity.upsert_app_user`` so the profile
    rail composes cleanly with the existing sub-user identity spine. Omitted fields preserve the
    current value; provided empty strings are stored as empty strings for text fields such as
    ``bio``.
    """
    if session_token is not None:
        user = app_identity.validate_session(conn, business_slug, session_token)
    elif app_user_id is not None:
        user = app_identity.get_app_user(conn, business_slug, app_user_id=app_user_id)
    elif email is not None:
        user = app_identity.upsert_app_user(conn, business_slug, email)
    else:
        raise ValueError("profile upsert requires app_user_id, email, or session_token")
    if user is None:
        raise AppProfileUserNotFound("app user not found")

    with conn.transaction():
        conn.execute(
            "insert into app_user_profiles (id, business_slug, display_name) "
            "values (%s, %s, %s) on conflict (id) do nothing",
            (user.id, business_slug, user.name),
        )
        row = conn.execute(
            "update app_user_profiles set "
            " display_name = coalesce(%s, display_name), "
            " headline = coalesce(%s, headline), "
            " bio = case when %s::text is null then bio else %s end, "
            " attributes = case when %s::jsonb is null then attributes else %s::jsonb end, "
            " metadata = case when %s::jsonb is null then metadata else %s::jsonb end, "
            " updated_at = now() "
            "where business_slug = %s and id = %s "
            f"returning {_PROFILE_COLUMNS}",
            (
                display_name,
                headline,
                bio,
                bio,
                None if attributes is None else _json_dumps(_normalize_object_field(attributes)),
                None if attributes is None else _json_dumps(_normalize_object_field(attributes)),
                None if metadata is None else _json_dumps(_normalize_object_field(metadata)),
                None if metadata is None else _json_dumps(_normalize_object_field(metadata)),
                business_slug,
                user.id,
            ),
        ).fetchone()
    return ResolvedAppProfile(user=user, profile=_profile_from_row(row))
