"""Postgres-backed Claude Agent SDK SessionStore adapter.

The SDK transcript format is opaque JSON. This adapter validates only the
stable storage envelope, serializes each session with a transaction advisory
lock, and relies on a partial unique index to deduplicate entries carrying the
same SDK UUID across append retries. Node never receives this connection.
"""

from __future__ import annotations

import copy
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Mapping, Sequence

from .claude_sdk_runtime import primary_sdk_session_project_key


MAX_APPEND_ENTRIES = 2_000
MAX_APPEND_BYTES = 8 * 1024 * 1024
MAX_ENTRY_BYTES = 1024 * 1024
MAX_LOAD_ENTRIES = 100_000
MAX_LOAD_BYTES = 64 * 1024 * 1024
DEFAULT_SESSION_RETENTION_DAYS = 90
MIN_SESSION_RETENTION_DAYS = 7
MAX_SESSION_RETENTION_DAYS = 3_650
MAX_PRUNE_SESSIONS = 100


class ClaudeSdkSessionStoreError(RuntimeError):
    """Durable SDK transcript storage failed closed."""


class ClaudeSdkSessionScopeError(ClaudeSdkSessionStoreError):
    """A SessionStore request escaped its bound owner/business/session scope."""


def _retention_cutoff(
    *,
    now: datetime | None,
    retention_days: int,
    batch_size: int,
) -> tuple[datetime, int]:
    try:
        days = int(retention_days)
    except (TypeError, ValueError) as exc:
        raise ClaudeSdkSessionStoreError(
            "SDK SessionStore retention_days must be an integer"
        ) from exc
    if not MIN_SESSION_RETENTION_DAYS <= days <= MAX_SESSION_RETENTION_DAYS:
        raise ClaudeSdkSessionStoreError(
            "SDK SessionStore retention_days must be between "
            f"{MIN_SESSION_RETENTION_DAYS} and {MAX_SESSION_RETENTION_DAYS}"
        )
    try:
        limit = int(batch_size)
    except (TypeError, ValueError) as exc:
        raise ClaudeSdkSessionStoreError(
            "SDK SessionStore prune batch_size must be an integer"
        ) from exc
    if not 1 <= limit <= MAX_PRUNE_SESSIONS:
        raise ClaudeSdkSessionStoreError(
            f"SDK SessionStore prune batch_size must be between 1 and {MAX_PRUNE_SESSIONS}"
        )
    observed_now = now or datetime.now(timezone.utc)
    if observed_now.tzinfo is None or observed_now.utcoffset() is None:
        raise ClaudeSdkSessionStoreError(
            "SDK SessionStore prune now must be timezone-aware"
        )
    return (
        observed_now.astimezone(timezone.utc) - timedelta(days=days),
        limit,
    )


def prune_expired_sdk_sessions_global(
    conn: Any,
    *,
    now: datetime | None = None,
    retention_days: int = DEFAULT_SESSION_RETENTION_DAYS,
    batch_size: int = MAX_PRUNE_SESSIONS,
) -> int:
    """Worker-only bounded sweep across every operator SDK session scope.

    ``conn`` must be the trusted operator worker connection; the table grant and
    forced RLS policy reject every other runtime role. Candidate discovery is
    global so a tenant that never resumes a session still receives retention.
    The final delete takes the same advisory lock as SessionStore append/load
    and repeats the age check, preserving a concurrently reactivated session.
    """

    cutoff, limit = _retention_cutoff(
        now=now,
        retention_days=retention_days,
        batch_size=batch_size,
    )
    pruned = 0
    with conn.transaction():
        candidates = conn.execute(
            "select owner_user_id::text, coalesce(business_slug, ''), "
            "project_key, session_id::text "
            "from public.agent_sdk_session_entries "
            "group by owner_user_id, business_slug, project_key, session_id "
            "having max(created_at) < %s::timestamptz "
            "order by max(created_at) asc, owner_user_id asc, "
            "business_slug asc nulls first, project_key asc, session_id asc "
            "limit %s",
            (cutoff, limit),
        ).fetchall()
        for row in candidates:
            if isinstance(row, Mapping):
                owner_user_id = str(row.get("owner_user_id") or "")
                business_slug = str(row.get("business_slug") or "")
                project_key = str(row.get("project_key") or "")
                session_id = str(row.get("session_id") or "")
            else:
                owner_user_id, business_slug, project_key, session_id = map(str, row)
            try:
                uuid.UUID(owner_user_id)
                uuid.UUID(session_id)
            except (ValueError, TypeError, AttributeError) as exc:
                raise ClaudeSdkSessionStoreError(
                    "SDK SessionStore retention candidate has an invalid UUID"
                ) from exc
            lock_key = "\x1f".join(
                (owner_user_id, business_slug, project_key, session_id)
            )
            conn.execute(
                "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (lock_key,),
            ).fetchone()
            deleted = conn.execute(
                "delete from public.agent_sdk_session_entries as target "
                "where target.owner_user_id = %s::uuid "
                "and target.business_slug is not distinct from %s "
                "and target.project_key = %s "
                "and target.session_id = %s::uuid "
                "and not exists ("
                "select 1 from public.agent_sdk_session_entries as fresh "
                "where fresh.owner_user_id = target.owner_user_id "
                "and fresh.business_slug is not distinct from target.business_slug "
                "and fresh.project_key = target.project_key "
                "and fresh.session_id = target.session_id "
                "and fresh.created_at >= %s::timestamptz"
                ") returning target.id",
                (
                    owner_user_id,
                    business_slug or None,
                    project_key,
                    session_id,
                    cutoff,
                ),
            ).fetchone()
            if deleted is not None:
                pruned += 1
    return pruned


class PostgresClaudeSdkSessionStore:
    """Durable, ordered, cross-host SessionStore for one operator business."""

    def __init__(
        self,
        *,
        operator_user_id: str,
        business_slug: str,
        connection_factory: Callable[[], Any] | None = None,
        retention_days: int = DEFAULT_SESSION_RETENTION_DAYS,
    ) -> None:
        self.operator_user_id = str(operator_user_id or "").strip()
        self.business_slug = str(business_slug or "").strip()
        try:
            uuid.UUID(self.operator_user_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ClaudeSdkSessionScopeError(
                "SDK SessionStore operator_user_id must be a UUID"
            ) from exc
        self.project_key = primary_sdk_session_project_key(
            operator_user_id=self.operator_user_id,
            business=self.business_slug,
        )
        try:
            self.retention_days = int(retention_days)
        except (TypeError, ValueError) as exc:
            raise ClaudeSdkSessionStoreError(
                "SDK SessionStore retention_days must be an integer"
            ) from exc
        if not MIN_SESSION_RETENTION_DAYS <= self.retention_days <= MAX_SESSION_RETENTION_DAYS:
            raise ClaudeSdkSessionStoreError(
                "SDK SessionStore retention_days must be between "
                f"{MIN_SESSION_RETENTION_DAYS} and {MAX_SESSION_RETENTION_DAYS}"
            )
        self._retention_pruned = False
        self._connection_factory = connection_factory

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        if self._connection_factory is not None:
            candidate = self._connection_factory()
            if hasattr(candidate, "__enter__"):
                with candidate as conn:
                    yield conn
            else:
                try:
                    yield candidate
                finally:
                    close = getattr(candidate, "close", None)
                    if callable(close):
                        close()
            return
        from .core import TakyonStore

        store = TakyonStore(operator_user_id=self.operator_user_id)
        with store._connect() as conn:
            with store._leaf_conn(conn) as raw:
                yield raw

    def _validated_key(self, raw: Mapping[str, str]) -> dict[str, str]:
        if not isinstance(raw, Mapping):
            raise ClaudeSdkSessionScopeError(
                "SDK SessionStore key must be an object"
            )
        project_key = str(raw.get("projectKey") or "").strip()
        session_text = str(raw.get("sessionId") or "").strip()
        subpath = str(raw.get("subpath") or "").strip()
        if project_key != self.project_key:
            raise ClaudeSdkSessionScopeError(
                "SDK SessionStore project scope mismatch"
            )
        try:
            session_id = str(uuid.UUID(session_text))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ClaudeSdkSessionScopeError(
                "SDK SessionStore sessionId must be a UUID"
            ) from exc
        if len(subpath) > 512 or subpath.startswith("/") or ".." in subpath.split("/"):
            raise ClaudeSdkSessionScopeError(
                "SDK SessionStore subpath is invalid"
            )
        return {
            "projectKey": project_key,
            "sessionId": session_id,
            **({"subpath": subpath} if subpath else {}),
        }

    @staticmethod
    def _encoded_entry(raw: Mapping[str, Any], index: int) -> tuple[dict[str, Any], bytes]:
        if not isinstance(raw, Mapping):
            raise ClaudeSdkSessionStoreError(
                f"SDK SessionStore entry {index} must be an object"
            )
        entry = copy.deepcopy(dict(raw))
        if not str(entry.get("type") or "").strip():
            raise ClaudeSdkSessionStoreError(
                f"SDK SessionStore entry {index} has no type"
            )
        try:
            encoded = json.dumps(
                entry,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ClaudeSdkSessionStoreError(
                f"SDK SessionStore entry {index} is not JSON-safe"
            ) from exc
        if len(encoded) > MAX_ENTRY_BYTES:
            raise ClaudeSdkSessionStoreError(
                f"SDK SessionStore entry {index} exceeds {MAX_ENTRY_BYTES} bytes"
            )
        return entry, encoded

    def _assert_owner(self, conn: Any) -> None:
        if self.business_slug:
            owner = conn.execute(
                "select 1 from public.businesses "
                "where slug = %s and owner_user_id = %s::uuid",
                (self.business_slug, self.operator_user_id),
            ).fetchone()
        else:
            owner = conn.execute(
                "select 1 from public.users where id = %s::uuid",
                (self.operator_user_id,),
            ).fetchone()
        if owner is None:
            raise ClaudeSdkSessionScopeError(
                "SDK SessionStore business ownership check failed"
            )

    def _lock_session(self, conn: Any, key: Mapping[str, str]) -> None:
        # One lock covers the main transcript and any subpaths. Appends and
        # resume loads therefore observe a stable, committed session ordering.
        lock_key = "\x1f".join(
            (
                self.operator_user_id,
                self.business_slug,
                key["projectKey"],
                key["sessionId"],
            )
        )
        conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (lock_key,),
        ).fetchone()

    def _lock_and_assert_owner(self, conn: Any, key: Mapping[str, str]) -> None:
        self._assert_owner(conn)
        self._lock_session(conn, key)

    def _prune_retention_once(self) -> None:
        if self._retention_pruned:
            return
        self.prune_expired_sessions()
        self._retention_pruned = True

    def prune_expired_sessions(
        self,
        *,
        now: datetime | None = None,
        retention_days: int | None = None,
        batch_size: int = MAX_PRUNE_SESSIONS,
    ) -> int:
        """Delete bounded, whole inactive sessions inside this exact tenant scope.

        The same per-session advisory lock used by append/load serializes the
        final age check with concurrent SDK writes. A newly active session is
        therefore retained even if it appeared in the initial candidate scan.
        """

        cutoff, limit = _retention_cutoff(
            now=now,
            retention_days=(
                self.retention_days if retention_days is None else retention_days
            ),
            batch_size=batch_size,
        )
        pruned = 0
        with self._connection() as conn:
            with conn.transaction():
                self._assert_owner(conn)
                candidates = conn.execute(
                    "select project_key, session_id "
                    "from public.agent_sdk_session_entries "
                    "where owner_user_id = %s::uuid "
                    "and business_slug is not distinct from %s "
                    "and project_key = %s "
                    "group by project_key, session_id "
                    "having max(created_at) < %s::timestamptz "
                    "order by max(created_at) asc, project_key asc, session_id asc "
                    "limit %s",
                    (
                        self.operator_user_id,
                        self.business_slug or None,
                        self.project_key,
                        cutoff,
                        limit,
                    ),
                ).fetchall()
                for row in candidates:
                    project_key = str(
                        row[0]
                        if not isinstance(row, Mapping)
                        else row.get("project_key") or ""
                    )
                    session_id = str(
                        row[1]
                        if not isinstance(row, Mapping)
                        else row.get("session_id") or ""
                    )
                    key = self._validated_key(
                        {"projectKey": project_key, "sessionId": session_id}
                    )
                    self._lock_session(conn, key)
                    deleted = conn.execute(
                        "delete from public.agent_sdk_session_entries as target "
                        "where target.owner_user_id = %s::uuid "
                        "and target.business_slug is not distinct from %s "
                        "and target.project_key = %s "
                        "and target.session_id = %s::uuid "
                        "and not exists ("
                        "select 1 from public.agent_sdk_session_entries as fresh "
                        "where fresh.owner_user_id = target.owner_user_id "
                        "and fresh.business_slug is not distinct from target.business_slug "
                        "and fresh.project_key = target.project_key "
                        "and fresh.session_id = target.session_id "
                        "and fresh.created_at >= %s::timestamptz"
                        ") returning target.id",
                        (
                            self.operator_user_id,
                            self.business_slug or None,
                            project_key,
                            session_id,
                            cutoff,
                        ),
                    ).fetchone()
                    if deleted is not None:
                        pruned += 1
        return pruned

    def append(
        self,
        key: Mapping[str, str],
        entries: Sequence[Mapping[str, Any]],
    ) -> None:
        validated = self._validated_key(key)
        if not isinstance(entries, Sequence) or isinstance(
            entries, (str, bytes, bytearray)
        ):
            raise ClaudeSdkSessionStoreError(
                "SDK SessionStore append entries must be an array"
            )
        if len(entries) > MAX_APPEND_ENTRIES:
            raise ClaudeSdkSessionStoreError(
                f"SDK SessionStore append exceeds {MAX_APPEND_ENTRIES} entries"
            )
        encoded_entries: list[tuple[dict[str, Any], bytes]] = []
        total_bytes = 0
        seen_batch_uuids: set[str] = set()
        for index, raw in enumerate(entries):
            entry, encoded = self._encoded_entry(raw, index)
            entry_uuid = str(entry.get("uuid") or "").strip()
            if entry_uuid and entry_uuid in seen_batch_uuids:
                continue
            if entry_uuid:
                seen_batch_uuids.add(entry_uuid)
            total_bytes += len(encoded)
            if total_bytes > MAX_APPEND_BYTES:
                raise ClaudeSdkSessionStoreError(
                    f"SDK SessionStore append exceeds {MAX_APPEND_BYTES} bytes"
                )
            encoded_entries.append((entry, encoded))
        if not encoded_entries:
            return
        self._prune_retention_once()
        subpath = str(validated.get("subpath") or "")
        with self._connection() as conn:
            with conn.transaction():
                self._lock_and_assert_owner(conn, validated)
                for entry_index, (entry, encoded) in enumerate(encoded_entries):
                    entry_uuid = str(entry.get("uuid") or "").strip() or None
                    conn.execute(
                        "insert into public.agent_sdk_session_entries "
                        "(owner_user_id, business_slug, project_key, session_id, "
                        "subpath, entry_index, entry_uuid, entry) "
                        "values (%s::uuid, %s, %s, %s::uuid, %s, %s, %s, %s::jsonb) "
                        "on conflict (owner_user_id, business_slug, project_key, "
                        "session_id, subpath, entry_uuid) where entry_uuid is not null "
                        "do nothing",
                        (
                            self.operator_user_id,
                            self.business_slug or None,
                            validated["projectKey"],
                            validated["sessionId"],
                            subpath,
                            entry_index,
                            entry_uuid,
                            encoded.decode("utf-8"),
                        ),
                    )

    def load(
        self, key: Mapping[str, str]
    ) -> list[dict[str, Any]] | None:
        validated = self._validated_key(key)
        self._prune_retention_once()
        subpath = str(validated.get("subpath") or "")
        with self._connection() as conn:
            with conn.transaction():
                self._lock_and_assert_owner(conn, validated)
                rows = conn.execute(
                    "select entry from public.agent_sdk_session_entries "
                    "where owner_user_id = %s::uuid and business_slug is not distinct from %s "
                    "and project_key = %s and session_id = %s::uuid and subpath = %s "
                    "order by id asc limit %s",
                    (
                        self.operator_user_id,
                        self.business_slug or None,
                        validated["projectKey"],
                        validated["sessionId"],
                        subpath,
                        MAX_LOAD_ENTRIES + 1,
                    ),
                ).fetchall()
        if not rows:
            return None
        if len(rows) > MAX_LOAD_ENTRIES:
            raise ClaudeSdkSessionStoreError(
                f"SDK SessionStore load exceeds {MAX_LOAD_ENTRIES} entries"
            )
        result: list[dict[str, Any]] = []
        total_bytes = 0
        seen_uuids: set[str] = set()
        for index, row in enumerate(rows):
            raw_entry = row[0] if not isinstance(row, Mapping) else row.get("entry")
            if isinstance(raw_entry, str):
                raw_entry = json.loads(raw_entry)
            entry, encoded = self._encoded_entry(raw_entry, index)
            entry_uuid = str(entry.get("uuid") or "").strip()
            if entry_uuid and entry_uuid in seen_uuids:
                raise ClaudeSdkSessionStoreError(
                    "SDK SessionStore durable UUID uniqueness invariant failed"
                )
            if entry_uuid:
                seen_uuids.add(entry_uuid)
            total_bytes += len(encoded)
            if total_bytes > MAX_LOAD_BYTES:
                raise ClaudeSdkSessionStoreError(
                    f"SDK SessionStore load exceeds {MAX_LOAD_BYTES} bytes"
                )
            result.append(entry)
        return result

    def list_subkeys(self, key: Mapping[str, str]) -> list[str]:
        validated = self._validated_key(key)
        self._prune_retention_once()
        if validated.get("subpath"):
            raise ClaudeSdkSessionScopeError(
                "SDK SessionStore list_subkeys key may not contain a subpath"
            )
        with self._connection() as conn:
            with conn.transaction():
                self._lock_and_assert_owner(conn, validated)
                rows = conn.execute(
                    "select distinct subpath from public.agent_sdk_session_entries "
                    "where owner_user_id = %s::uuid and business_slug is not distinct from %s "
                    "and project_key = %s and session_id = %s::uuid and subpath <> '' "
                    "order by subpath asc limit 2001",
                    (
                        self.operator_user_id,
                        self.business_slug or None,
                        validated["projectKey"],
                        validated["sessionId"],
                    ),
                ).fetchall()
        if len(rows) > 2_000:
            raise ClaudeSdkSessionStoreError(
                "SDK SessionStore has too many subpaths"
            )
        return [
            str(row[0] if not isinstance(row, Mapping) else row.get("subpath") or "")
            for row in rows
        ]

    def delete(self, key: Mapping[str, str]) -> None:
        """Delete the main transcript and all SDK-owned subkeys for one session."""

        validated = self._validated_key(key)
        if validated.get("subpath"):
            raise ClaudeSdkSessionScopeError(
                "SDK SessionStore delete key may not contain a subpath"
            )
        with self._connection() as conn:
            with conn.transaction():
                self._lock_and_assert_owner(conn, validated)
                conn.execute(
                    "delete from public.agent_sdk_session_entries "
                    "where owner_user_id = %s::uuid "
                    "and business_slug is not distinct from %s "
                    "and project_key = %s and session_id = %s::uuid",
                    (
                        self.operator_user_id,
                        self.business_slug or None,
                        validated["projectKey"],
                        validated["sessionId"],
                    ),
                )

    def delete_session_all_scopes(self, session_id: str) -> None:
        """Delete one operator chat transcript across global/business scopes."""

        try:
            stable_session = str(uuid.UUID(str(session_id or "").strip()))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ClaudeSdkSessionScopeError(
                "SDK SessionStore sessionId must be a UUID"
            ) from exc
        with self._connection() as conn:
            with conn.transaction():
                owner = conn.execute(
                    "select 1 from public.users where id = %s::uuid",
                    (self.operator_user_id,),
                ).fetchone()
                if owner is None:
                    raise ClaudeSdkSessionScopeError(
                        "SDK SessionStore operator ownership check failed"
                    )
                conn.execute(
                    "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{self.operator_user_id}\x1f{stable_session}",),
                ).fetchone()
                conn.execute(
                    "delete from public.agent_sdk_session_entries "
                    "where owner_user_id = %s::uuid and session_id = %s::uuid",
                    (self.operator_user_id, stable_session),
                )


__all__ = [
    "ClaudeSdkSessionScopeError",
    "ClaudeSdkSessionStoreError",
    "PostgresClaudeSdkSessionStore",
]
