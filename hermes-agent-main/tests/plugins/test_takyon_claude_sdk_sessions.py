from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from plugins.takyon.claude_sdk_sessions import (
    ClaudeSdkSessionScopeError,
    ClaudeSdkSessionStoreError,
    PostgresClaudeSdkSessionStore,
    prune_expired_sdk_sessions_global,
)


class _Cursor:
    def __init__(self, *, one=None, many=None):
        self._one = one
        self._many = list(many or [])

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)


class _SessionConn:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.locks: list[str] = []
        self.next_id = 1
        self.now = datetime.now(timezone.utc)
        self.retention_scans = 0
        self.retention_scan_params: list[tuple[object, ...]] = []
        self.retention_deletes: list[tuple[object, ...]] = []
        self.lock_hook = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def transaction(self):
        return nullcontext()

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split()).lower()
        if normalized.startswith("select 1 from public.businesses"):
            return _Cursor(one=(1,))
        if normalized.startswith("select 1 from public.users"):
            return _Cursor(one=(1,))
        if normalized.startswith("select 1 from public.agent_sdk_session_entries"):
            owner, business, project, session = params
            exists = any(
                row["owner"] == owner
                and row["business"] == business
                and row["project"] == project
                and row["session"] == session
                and row["subpath"] == ""
                for row in self.rows
            )
            return _Cursor(one=(1,) if exists else None)
        if "pg_advisory_xact_lock" in normalized:
            self.locks.append(str(params[0]))
            if callable(self.lock_hook):
                self.lock_hook(str(params[0]))
            return _Cursor(one=(True,))
        if normalized.startswith("insert into public.agent_sdk_session_entries"):
            owner, business, project, session, subpath, index, entry_uuid, encoded = params
            duplicate = bool(
                entry_uuid
                and any(
                    row["owner"] == owner
                    and row["business"] == business
                    and row["project"] == project
                    and row["session"] == session
                    and row["subpath"] == subpath
                    and row["entry_uuid"] == entry_uuid
                    for row in self.rows
                )
            )
            if not duplicate:
                self.rows.append(
                    {
                        "id": self.next_id,
                        "owner": owner,
                        "business": business,
                        "project": project,
                        "session": session,
                        "subpath": subpath,
                        "index": index,
                        "entry_uuid": entry_uuid,
                        "entry": json.loads(encoded),
                        "created_at": self.now,
                    }
                )
                self.next_id += 1
            return _Cursor()
        if normalized.startswith(
            "select owner_user_id::text, coalesce(business_slug, '')"
        ):
            cutoff, limit = params
            self.retention_scans += 1
            self.retention_scan_params.append(tuple(params))
            grouped: dict[tuple[str, str | None, str, str], datetime] = {}
            for row in self.rows:
                group = (
                    row["owner"],
                    row["business"],
                    row["project"],
                    row["session"],
                )
                grouped[group] = max(
                    grouped.get(group, row["created_at"]), row["created_at"]
                )
            candidates = sorted(
                (
                    (last_seen, owner, business or "", project, session)
                    for (owner, business, project, session), last_seen in grouped.items()
                    if last_seen < cutoff
                ),
                key=lambda item: item,
            )[: int(limit)]
            return _Cursor(
                many=[
                    (owner, business, project, session)
                    for _last_seen, owner, business, project, session in candidates
                ]
            )
        if normalized.startswith("select project_key, session_id"):
            owner, business, project, cutoff, limit = params
            self.retention_scans += 1
            self.retention_scan_params.append(tuple(params))
            grouped: dict[tuple[str, str], datetime] = {}
            for row in self.rows:
                if (
                    row["owner"] == owner
                    and row["business"] == business
                    and row["project"] == project
                ):
                    group = (row["project"], row["session"])
                    grouped[group] = max(
                        grouped.get(group, row["created_at"]), row["created_at"]
                    )
            candidates = sorted(
                (
                    (last_seen, project_key, session_id)
                    for (project_key, session_id), last_seen in grouped.items()
                    if last_seen < cutoff
                ),
                key=lambda item: item,
            )[: int(limit)]
            return _Cursor(
                many=[
                    (project_key, session_id)
                    for _last_seen, project_key, session_id in candidates
                ]
            )
        if normalized.startswith("select entry from public.agent_sdk_session_entries"):
            owner, business, project, session, subpath, _limit = params
            rows = sorted(
                (
                    row
                    for row in self.rows
                    if row["owner"] == owner
                    and row["business"] == business
                    and row["project"] == project
                    and row["session"] == session
                    and row["subpath"] == subpath
                ),
                key=lambda row: row["id"],
            )
            return _Cursor(many=[(row["entry"],) for row in rows])
        if normalized.startswith("select distinct subpath"):
            owner, business, project, session = params
            subpaths = sorted(
                {
                    row["subpath"]
                    for row in self.rows
                    if row["owner"] == owner
                    and row["business"] == business
                    and row["project"] == project
                    and row["session"] == session
                    and row["subpath"]
                }
            )
            return _Cursor(many=[(subpath,) for subpath in subpaths])
        if normalized.startswith(
            "delete from public.agent_sdk_session_entries as target"
        ):
            owner, business, project, session, cutoff = params
            self.retention_deletes.append(tuple(params))
            matching = [
                row
                for row in self.rows
                if row["owner"] == owner
                and row["business"] == business
                and row["project"] == project
                and row["session"] == session
            ]
            if any(row["created_at"] >= cutoff for row in matching):
                return _Cursor(one=None)
            deleted_id = matching[0]["id"] if matching else None
            self.rows = [row for row in self.rows if row not in matching]
            return _Cursor(one=(deleted_id,) if deleted_id is not None else None)
        if normalized.startswith("delete from public.agent_sdk_session_entries"):
            if len(params) == 2:
                owner, session = params
                self.rows = [
                    row
                    for row in self.rows
                    if not (row["owner"] == owner and row["session"] == session)
                ]
                return _Cursor()
            owner, business, project, session = params
            self.rows = [
                row
                for row in self.rows
                if not (
                    row["owner"] == owner
                    and row["business"] == business
                    and row["project"] == project
                    and row["session"] == session
                )
            ]
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {sql}")


def _store(conn: _SessionConn):
    owner = str(uuid.uuid4())
    store = PostgresClaudeSdkSessionStore(
        operator_user_id=owner,
        business_slug="acme",
        connection_factory=lambda: conn,
    )
    key = {
        "projectKey": store.project_key,
        "sessionId": str(uuid.uuid4()),
    }
    return store, key


def test_postgres_session_store_preserves_order_and_deduplicates_uuid_retries() -> None:
    conn = _SessionConn()
    store, key = _store(conn)
    store.append(
        key,
        [
            {"type": "user", "uuid": "u-1", "message": "first"},
            {"type": "assistant", "uuid": "u-2", "message": "second"},
        ],
    )
    store.append(
        key,
        [
            {"type": "user", "uuid": "u-1", "message": "duplicate retry"},
            {"type": "system", "message": "no uuid remains append-only"},
        ],
    )

    assert store.load(key) == [
        {"type": "user", "uuid": "u-1", "message": "first"},
        {"type": "assistant", "uuid": "u-2", "message": "second"},
        {"type": "system", "message": "no uuid remains append-only"},
    ]
    assert len(conn.locks) == 3
    assert len(set(conn.locks)) == 1


def test_postgres_session_store_scopes_project_session_and_subpaths() -> None:
    conn = _SessionConn()
    store, key = _store(conn)
    subkey = {**key, "subpath": "subagents/agent-a"}
    with pytest.raises(ClaudeSdkSessionScopeError):
        store.load({**key, "projectKey": "other-tenant"})
    assert conn.retention_scans == 0
    store.append(subkey, [{"type": "assistant", "uuid": "a-1"}])

    assert store.load(key) is None
    assert store.load(subkey) == [{"type": "assistant", "uuid": "a-1"}]
    assert store.list_subkeys(key) == ["subagents/agent-a"]
    with pytest.raises(ClaudeSdkSessionScopeError):
        store.load({**key, "sessionId": str(uuid.uuid4()), "subpath": "../escape"})


def test_session_resume_evidence_requires_a_committed_main_transcript() -> None:
    conn = _SessionConn()
    store, key = _store(conn)

    assert store.has_durable_transcript(key) is False
    store.append(
        {**key, "subpath": "subagents/agent-a"},
        [{"type": "assistant", "uuid": "subagent-only"}],
    )
    assert store.has_durable_transcript(key) is False
    store.append(key, [{"type": "user", "uuid": "sdk-transcript"}])
    assert store.has_durable_transcript(key) is True


def test_postgres_session_store_supports_global_scope_and_deletes_all_subkeys() -> None:
    conn = _SessionConn()
    owner = str(uuid.uuid4())
    store = PostgresClaudeSdkSessionStore(
        operator_user_id=owner,
        business_slug="",
        connection_factory=lambda: conn,
    )
    key = {"projectKey": store.project_key, "sessionId": str(uuid.uuid4())}
    store.append(key, [{"type": "user", "uuid": "root-1"}])
    store.append({**key, "subpath": "state/one"}, [{"type": "assistant", "uuid": "root-2"}])

    assert store.load(key) == [{"type": "user", "uuid": "root-1"}]
    assert store.list_subkeys(key) == ["state/one"]
    store.delete(key)
    assert store.load(key) is None
    assert store.list_subkeys(key) == []


def test_postgres_session_store_deletes_ui_session_across_business_scopes() -> None:
    conn = _SessionConn()
    owner = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    global_store = PostgresClaudeSdkSessionStore(
        operator_user_id=owner,
        business_slug="",
        connection_factory=lambda: conn,
    )
    business_store = PostgresClaudeSdkSessionStore(
        operator_user_id=owner,
        business_slug="acme",
        connection_factory=lambda: conn,
    )
    global_store.append(
        {"projectKey": global_store.project_key, "sessionId": session_id},
        [{"type": "user", "uuid": "global"}],
    )
    business_store.append(
        {"projectKey": business_store.project_key, "sessionId": session_id},
        [{"type": "user", "uuid": "business"}],
    )

    global_store.delete_session_all_scopes(session_id)

    assert global_store.load(
        {"projectKey": global_store.project_key, "sessionId": session_id}
    ) is None
    assert business_store.load(
        {"projectKey": business_store.project_key, "sessionId": session_id}
    ) is None


def test_postgres_session_store_enforces_entry_and_batch_bounds(monkeypatch) -> None:
    from plugins.takyon import claude_sdk_sessions as sessions

    conn = _SessionConn()
    store, key = _store(conn)
    monkeypatch.setattr(sessions, "MAX_ENTRY_BYTES", 32)
    with pytest.raises(ClaudeSdkSessionStoreError, match="exceeds 32 bytes"):
        store.append(key, [{"type": "user", "message": "x" * 100}])

    monkeypatch.setattr(sessions, "MAX_ENTRY_BYTES", 1024)
    monkeypatch.setattr(sessions, "MAX_APPEND_ENTRIES", 1)
    with pytest.raises(ClaudeSdkSessionStoreError, match="exceeds 1 entries"):
        store.append(key, [{"type": "user"}, {"type": "assistant"}])


def test_session_retention_prunes_whole_expired_sessions_inside_exact_scope() -> None:
    conn = _SessionConn()
    store, expired_key = _store(conn)
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    conn.now = now - timedelta(days=120)
    store.append(expired_key, [{"type": "user", "uuid": "old-main"}])
    store.append(
        {**expired_key, "subpath": "subagents/old"},
        [{"type": "assistant", "uuid": "old-sub"}],
    )

    active_key = {**expired_key, "sessionId": str(uuid.uuid4())}
    conn.now = now - timedelta(days=1)
    store.append(active_key, [{"type": "user", "uuid": "active"}])

    other_store = PostgresClaudeSdkSessionStore(
        operator_user_id=store.operator_user_id,
        business_slug="other-business",
        connection_factory=lambda: conn,
    )
    other_key = {
        "projectKey": other_store.project_key,
        "sessionId": str(uuid.uuid4()),
    }
    conn.now = now - timedelta(days=120)
    other_store.append(other_key, [{"type": "user", "uuid": "other-old"}])

    assert store.prune_expired_sessions(now=now) == 1
    assert not any(row["session"] == expired_key["sessionId"] for row in conn.rows)
    assert any(row["session"] == active_key["sessionId"] for row in conn.rows)
    assert any(row["session"] == other_key["sessionId"] for row in conn.rows)
    assert conn.retention_deletes[-1][0:4] == (
        store.operator_user_id,
        "acme",
        store.project_key,
        expired_key["sessionId"],
    )
    assert conn.retention_scan_params[-1][0:3] == (
        store.operator_user_id,
        "acme",
        store.project_key,
    )


def test_session_retention_rechecks_freshness_after_session_lock() -> None:
    conn = _SessionConn()
    store, key = _store(conn)
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    conn.now = now - timedelta(days=120)
    store.append(key, [{"type": "user", "uuid": "old"}])

    def concurrent_append_won_lock(lock_key: str) -> None:
        if lock_key.endswith(key["sessionId"]):
            for row in conn.rows:
                if row["session"] == key["sessionId"]:
                    row["created_at"] = now

    conn.lock_hook = concurrent_append_won_lock
    assert store.prune_expired_sessions(now=now) == 0
    assert any(row["session"] == key["sessionId"] for row in conn.rows)
    assert conn.retention_deletes


def test_worker_retention_sweeps_abandoned_sessions_across_all_scopes() -> None:
    conn = _SessionConn()
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    first_store, first_key = _store(conn)
    second_store = PostgresClaudeSdkSessionStore(
        operator_user_id=str(uuid.uuid4()),
        business_slug="second-business",
        connection_factory=lambda: conn,
    )
    second_key = {
        "projectKey": second_store.project_key,
        "sessionId": str(uuid.uuid4()),
    }
    conn.now = now - timedelta(days=120)
    first_store.append(first_key, [{"type": "user", "uuid": "first-old"}])
    second_store.append(second_key, [{"type": "user", "uuid": "second-old"}])
    active_key = {**first_key, "sessionId": str(uuid.uuid4())}
    conn.now = now - timedelta(days=1)
    first_store.append(active_key, [{"type": "user", "uuid": "active"}])

    assert prune_expired_sdk_sessions_global(conn, now=now) == 2
    assert {row["session"] for row in conn.rows} == {active_key["sessionId"]}
    assert {params[0] for params in conn.retention_deletes[-2:]} == {
        first_store.operator_user_id,
        second_store.operator_user_id,
    }


def test_worker_retention_rechecks_freshness_under_shared_session_lock() -> None:
    conn = _SessionConn()
    store, key = _store(conn)
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    conn.now = now - timedelta(days=120)
    store.append(key, [{"type": "user", "uuid": "old"}])

    def concurrent_append_won_lock(lock_key: str) -> None:
        if lock_key.endswith(key["sessionId"]):
            for row in conn.rows:
                if row["session"] == key["sessionId"]:
                    row["created_at"] = now

    conn.lock_hook = concurrent_append_won_lock
    assert prune_expired_sdk_sessions_global(conn, now=now) == 0
    assert any(row["session"] == key["sessionId"] for row in conn.rows)


def test_session_retention_runs_once_before_first_resume_load() -> None:
    conn = _SessionConn()
    original, key = _store(conn)
    now = datetime.now(timezone.utc)
    conn.now = now - timedelta(days=120)
    original.append(key, [{"type": "user", "uuid": "expired"}])

    resumed = PostgresClaudeSdkSessionStore(
        operator_user_id=original.operator_user_id,
        business_slug="acme",
        connection_factory=lambda: conn,
    )
    scans_before = conn.retention_scans
    assert resumed.load(key) is None
    assert conn.retention_scans == scans_before + 1
    assert resumed.list_subkeys(key) == []
    assert conn.retention_scans == scans_before + 1


def test_session_retention_rejects_destructive_or_unbounded_policy() -> None:
    conn = _SessionConn()
    owner = str(uuid.uuid4())
    with pytest.raises(ClaudeSdkSessionStoreError, match="between 7 and 3650"):
        PostgresClaudeSdkSessionStore(
            operator_user_id=owner,
            business_slug="acme",
            connection_factory=lambda: conn,
            retention_days=0,
        )
    store, _key = _store(conn)
    with pytest.raises(ClaudeSdkSessionStoreError, match="batch_size"):
        store.prune_expired_sessions(batch_size=101)
    with pytest.raises(ClaudeSdkSessionStoreError, match="timezone-aware"):
        store.prune_expired_sessions(now=datetime(2026, 7, 13))


def test_session_store_migration_matches_backend_security_contract() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "plugins"
        / "takyon"
        / "db"
        / "migrations"
        / "0089_claude_sdk_session_store.sql"
    ).read_text(encoding="utf-8").lower()

    assert "project_key" in migration
    assert "entry_uuid" in migration
    assert "where entry_uuid is not null" in migration
    assert "force row level security" in migration
    assert "takyon_app_runtime" in migration
    assert "takyon_safebox_authority" in migration
    assert "revoke all on table public.agent_sdk_session_entries" in migration
    assert "revoke all on sequence public.agent_sdk_session_entries_id_seq" in migration
    assert "business_slug   text references" in migration
    assert "nulls not distinct" in migration
    assert "grant select, insert, delete on public.agent_sdk_session_entries" in migration
    assert "append_id" not in migration


def test_session_retention_migration_adds_scoped_and_global_age_indexes_only() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "plugins"
        / "takyon"
        / "db"
        / "migrations"
        / "0092_claude_sdk_session_retention.sql"
    ).read_text(encoding="utf-8").lower()

    assert "agent_sdk_session_entries_retention_idx" in migration
    assert (
        "owner_user_id, business_slug, project_key, session_id, created_at desc"
        in migration
    )
    assert "agent_sdk_session_entries_global_retention_idx" in migration
    assert (
        "created_at, owner_user_id, business_slug, project_key, session_id"
        in migration
    )
    assert "delete from" not in migration
    assert "grant " not in migration
