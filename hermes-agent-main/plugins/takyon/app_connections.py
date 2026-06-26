"""Generic directed cross-user connection rail on the shared app plane.

This leaf owns one concern: persist directed relationship state between two
subusers in the same business. It is deliberately generic rather than
dating-specific — products can interpret the states as likes, passes, blocks,
or another mutual-interest loop without overloading records or private
profiles.
"""

from __future__ import annotations

from plugins.takyon import app_identity

_STATE_CHOICES = frozenset({"like", "pass", "block"})
_LIST_STATE_CHOICES = frozenset({"matches", "likes", "passes", "blocks"})


class AppConnectionError(Exception):
    """Base for app connection errors."""


class AppConnectionUserNotFound(AppConnectionError):
    """The acting subuser could not be resolved in this business."""


class AppConnectionTargetNotFound(AppConnectionError):
    """The target subuser is absent or unavailable."""


def _reject_session_identity_override(
    *,
    session_token: str | None,
    app_user_id: str | None,
    email: str | None,
) -> None:
    if session_token is not None and (app_user_id is not None or email is not None):
        raise ValueError("session_token is authoritative; omit app_user_id/email")


def _normalize_state(value: str) -> str:
    state = str(value or "").strip().lower().replace("-", "_")
    if state not in _STATE_CHOICES:
        raise ValueError("connection state must be one of like, pass, or block")
    return state


def _normalize_action(value: str) -> str:
    action = str(value or "").strip().lower().replace("-", "_")
    if action in _STATE_CHOICES or action == "unblock":
        return action
    raise ValueError("connection action must be one of like, pass, block, or unblock")


def _normalize_list_state(value: str | None) -> str:
    if value in {None, ""}:
        return "matches"
    state = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "like": "likes",
        "pass": "passes",
        "block": "blocks",
        "match": "matches",
    }
    state = aliases.get(state, state)
    if state not in _LIST_STATE_CHOICES:
        raise ValueError("connections state must be one of matches, likes, passes, or blocks")
    return state


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
    raise ValueError("connection lookup requires app_user_id, email, or session_token")


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
        raise ValueError("connection write requires app_user_id, email, or session_token")
    if user is None:
        raise AppConnectionUserNotFound("app user not found")
    return user


def _target_directory_snapshot(conn, business_slug: str, *, viewer_id: str, target_id: str) -> dict | None:
    row = conn.execute(
        "select p.id, p.business_slug, p.directory_enabled, p.directory_profile, "
        "p.created_at, coalesce(p.directory_updated_at, p.updated_at) "
        "from app_user_profiles p "
        "join app_users u on u.business_slug = p.business_slug and u.id = p.id "
        "where p.business_slug = %s "
        "  and p.id = %s "
        "  and p.directory_enabled = true "
        "  and u.status = 'active' "
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
        (business_slug, target_id, viewer_id, viewer_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "app_user_id": str(row[0]),
        "business_slug": str(row[1]),
        "enabled": bool(row[2]),
        "profile": row[3] if isinstance(row[3], dict) else {},
        "created_at": row[4],
        "updated_at": row[5],
    }


def _is_match(conn, business_slug: str, *, source_id: str, target_id: str) -> bool:
    row = conn.execute(
        "select 1 "
        "from app_connections c "
        "join app_connections r "
        "  on r.business_slug = c.business_slug "
        " and r.source_app_user_id = c.target_app_user_id "
        " and r.target_app_user_id = c.source_app_user_id "
        " and r.state = 'like' "
        "where c.business_slug = %s "
        "  and c.source_app_user_id = %s "
        "  and c.target_app_user_id = %s "
        "  and c.state = 'like' "
        "  and not exists ("
        "    select 1 from app_connections b "
        "    where b.business_slug = c.business_slug "
        "      and b.state = 'block' "
        "      and ("
        "        (b.source_app_user_id = c.source_app_user_id and b.target_app_user_id = c.target_app_user_id) "
        "        or (b.source_app_user_id = c.target_app_user_id and b.target_app_user_id = c.source_app_user_id)"
        "      )"
        "  ) "
        "limit 1",
        (business_slug, source_id, target_id),
    ).fetchone()
    return row is not None


def set_connection(
    conn,
    business_slug: str,
    *,
    target_app_user_id: str,
    action: str,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
) -> tuple[app_identity.AppUser, dict]:
    user = _resolve_writable_user(
        conn,
        business_slug,
        app_user_id=app_user_id,
        email=email,
        session_token=session_token,
    )
    normalized_action = _normalize_action(action)
    target = app_identity.get_app_user(conn, business_slug, app_user_id=target_app_user_id)
    if target is None or str(target.status or "active") != "active":
        raise AppConnectionTargetNotFound("app connection target not found")
    if target.id == user.id:
        raise ValueError("target_app_user_id must not be the current app user")
    visible_target = None
    if normalized_action in {"like", "pass"}:
        visible_target = _target_directory_snapshot(
            conn,
            business_slug,
            viewer_id=user.id,
            target_id=target.id,
        )
        if visible_target is None:
            raise AppConnectionTargetNotFound("app connection target not found")
    result: dict
    with conn.transaction():
        existing = conn.execute(
            "select state, created_at, updated_at from app_connections "
            "where business_slug = %s and source_app_user_id = %s and target_app_user_id = %s",
            (business_slug, user.id, target.id),
        ).fetchone()
        if normalized_action == "unblock":
            deleted = False
            if existing is not None and str(existing[0] or "") == "block":
                conn.execute(
                    "delete from app_connections where business_slug = %s and source_app_user_id = %s and target_app_user_id = %s",
                    (business_slug, user.id, target.id),
                )
                deleted = True
            result = {
                "business_slug": business_slug,
                "source_app_user_id": user.id,
                "target_app_user_id": target.id,
                "state": "neutral",
                "matched": False,
                "created_at": "",
                "updated_at": "",
                "deleted": deleted,
                "target": _target_directory_snapshot(conn, business_slug, viewer_id=user.id, target_id=target.id),
            }
        else:
            normalized_state = _normalize_state(normalized_action)
            if existing is None:
                row = conn.execute(
                    "insert into app_connections (business_slug, source_app_user_id, target_app_user_id, state, created_at, updated_at) "
                    "values (%s, %s, %s, %s, now(), now()) "
                    "returning business_slug, source_app_user_id, target_app_user_id, state, created_at, updated_at",
                    (business_slug, user.id, target.id, normalized_state),
                ).fetchone()
            else:
                row = conn.execute(
                    "update app_connections set state = %s, updated_at = now() "
                    "where business_slug = %s and source_app_user_id = %s and target_app_user_id = %s "
                    "returning business_slug, source_app_user_id, target_app_user_id, state, created_at, updated_at",
                    (normalized_state, business_slug, user.id, target.id),
                ).fetchone()
            result = {
                "business_slug": str(row[0]),
                "source_app_user_id": str(row[1]),
                "target_app_user_id": str(row[2]),
                "state": str(row[3]),
                "matched": _is_match(conn, business_slug, source_id=user.id, target_id=target.id),
                "created_at": row[4],
                "updated_at": row[5],
                "target": (
                    visible_target
                    if visible_target is not None
                    else _target_directory_snapshot(conn, business_slug, viewer_id=user.id, target_id=target.id)
                ),
            }
    return user, result


def list_connections(
    conn,
    business_slug: str,
    *,
    state: str | None = None,
    app_user_id: str | None = None,
    email: str | None = None,
    session_token: str | None = None,
    limit: int = 50,
) -> tuple[app_identity.AppUser, list[dict]] | None:
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
    limit_value = max(1, min(limit, 100))
    list_state = _normalize_list_state(state)
    if list_state == "matches":
        rows = conn.execute(
            "select c.business_slug, c.source_app_user_id, c.target_app_user_id, c.state, "
            "greatest(c.updated_at, r.updated_at), c.created_at "
            "from app_connections c "
            "join app_connections r "
            "  on r.business_slug = c.business_slug "
            " and r.source_app_user_id = c.target_app_user_id "
            " and r.target_app_user_id = c.source_app_user_id "
            " and r.state = 'like' "
            "join app_users u on u.business_slug = c.business_slug and u.id = c.target_app_user_id "
            "join app_user_profiles p on p.business_slug = c.business_slug and p.id = c.target_app_user_id and p.directory_enabled = true "
            "where c.business_slug = %s "
            "  and c.source_app_user_id = %s "
            "  and c.state = 'like' "
            "  and u.status = 'active' "
            "  and not exists ("
            "    select 1 from app_connections b "
            "    where b.business_slug = c.business_slug "
            "      and b.state = 'block' "
            "      and ("
            "        (b.source_app_user_id = c.source_app_user_id and b.target_app_user_id = c.target_app_user_id) "
            "        or (b.source_app_user_id = c.target_app_user_id and b.target_app_user_id = c.source_app_user_id)"
            "      )"
            "  ) "
            "order by greatest(c.updated_at, r.updated_at) desc, c.target_app_user_id asc "
            "limit %s",
            (business_slug, user.id, limit_value),
        ).fetchall()
        items = []
        for row in rows:
            target_id = str(row[2])
            items.append(
                {
                    "business_slug": str(row[0]),
                    "source_app_user_id": str(row[1]),
                    "target_app_user_id": target_id,
                    "state": "like",
                    "matched": True,
                    "updated_at": row[4],
                    "created_at": row[5],
                    "target": _target_directory_snapshot(conn, business_slug, viewer_id=user.id, target_id=target_id),
                }
            )
        return user, items

    row_state = {"likes": "like", "passes": "pass", "blocks": "block"}[list_state]
    rows = conn.execute(
        "select business_slug, source_app_user_id, target_app_user_id, state, created_at, updated_at "
        "from app_connections "
        "where business_slug = %s and source_app_user_id = %s and state = %s "
        "order by updated_at desc, target_app_user_id asc "
        "limit %s",
        (business_slug, user.id, row_state, limit_value),
    ).fetchall()
    items = []
    for row in rows:
        target_id = str(row[2])
        items.append(
            {
                "business_slug": str(row[0]),
                "source_app_user_id": str(row[1]),
                "target_app_user_id": target_id,
                "state": str(row[3]),
                "matched": (
                    row_state == "like"
                    and _is_match(conn, business_slug, source_id=user.id, target_id=target_id)
                ),
                "created_at": row[4],
                "updated_at": row[5],
                "target": (
                    None
                    if row_state == "block"
                    else _target_directory_snapshot(conn, business_slug, viewer_id=user.id, target_id=target_id)
                ),
            }
        )
    return user, items
