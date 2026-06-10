import asyncio
import os

import pytest

from gateway.config import Platform
from gateway.run import GatewayRunner
from gateway.session import SessionContext, SessionSource
from gateway.session_context import (
    get_session_env,
    set_session_vars,
    clear_session_vars,
    _VAR_MAP,
    _UNSET,
)
import plugins.takyon.core as takyon_core
from plugins.takyon.core import TakyonStore
from plugins.takyon import storage


@pytest.fixture(autouse=True)
def _reset_contextvars():
    """Reset all session contextvars to _UNSET between tests.

    In production each asyncio.Task gets a fresh context copy where the
    defaults are _UNSET.  In tests all functions share the same thread
    context, so a clear_session_vars() from test A (which sets vars to "")
    would leak into test B.  This fixture ensures each test starts clean.
    """
    yield
    for var in _VAR_MAP.values():
        # Can't use var.reset() without a token; just set back to sentinel.
        var.set(_UNSET)


def test_set_session_env_sets_contextvars(monkeypatch):
    """_set_session_env should populate contextvars, not os.environ."""
    runner = object.__new__(GatewayRunner)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_name="Group",
        chat_type="group",
        user_id="123456",
        user_name="alice",
        thread_id="17585",
    )
    context = SessionContext(source=source, connected_platforms=[], home_channels={})

    monkeypatch.delenv("TAKYON_SESSION_PLATFORM", raising=False)
    monkeypatch.delenv("TAKYON_SESSION_CHAT_ID", raising=False)
    monkeypatch.delenv("TAKYON_SESSION_CHAT_NAME", raising=False)
    monkeypatch.delenv("TAKYON_SESSION_USER_ID", raising=False)
    monkeypatch.delenv("TAKYON_SESSION_USER_NAME", raising=False)
    monkeypatch.delenv("TAKYON_SESSION_THREAD_ID", raising=False)

    tokens = runner._set_session_env(context)

    # Values should be readable via get_session_env (contextvar path)
    assert get_session_env("TAKYON_SESSION_PLATFORM") == "telegram"
    assert get_session_env("TAKYON_SESSION_CHAT_ID") == "-1001"
    assert get_session_env("TAKYON_SESSION_CHAT_NAME") == "Group"
    assert get_session_env("TAKYON_SESSION_USER_ID") == "123456"
    assert get_session_env("TAKYON_SESSION_USER_NAME") == "alice"
    assert get_session_env("TAKYON_SESSION_THREAD_ID") == "17585"

    # os.environ should NOT be touched
    assert os.getenv("TAKYON_SESSION_PLATFORM") is None
    assert os.getenv("TAKYON_SESSION_THREAD_ID") is None

    # Clean up
    runner._clear_session_env(tokens)


def test_clear_session_env_restores_previous_state(monkeypatch):
    """_clear_session_env should restore contextvars to their pre-handler values."""
    runner = object.__new__(GatewayRunner)

    monkeypatch.delenv("TAKYON_SESSION_PLATFORM", raising=False)
    monkeypatch.delenv("TAKYON_SESSION_CHAT_ID", raising=False)
    monkeypatch.delenv("TAKYON_SESSION_CHAT_NAME", raising=False)
    monkeypatch.delenv("TAKYON_SESSION_USER_ID", raising=False)
    monkeypatch.delenv("TAKYON_SESSION_USER_NAME", raising=False)
    monkeypatch.delenv("TAKYON_SESSION_THREAD_ID", raising=False)

    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_name="Group",
        chat_type="group",
        user_id="123456",
        user_name="alice",
        thread_id="17585",
    )
    context = SessionContext(source=source, connected_platforms=[], home_channels={})

    tokens = runner._set_session_env(context)
    assert get_session_env("TAKYON_SESSION_PLATFORM") == "telegram"
    assert get_session_env("TAKYON_SESSION_USER_ID") == "123456"

    runner._clear_session_env(tokens)

    # After clear, contextvars should return to defaults (empty)
    assert get_session_env("TAKYON_SESSION_PLATFORM") == ""
    assert get_session_env("TAKYON_SESSION_CHAT_ID") == ""
    assert get_session_env("TAKYON_SESSION_CHAT_NAME") == ""
    assert get_session_env("TAKYON_SESSION_USER_ID") == ""
    assert get_session_env("TAKYON_SESSION_USER_NAME") == ""
    assert get_session_env("TAKYON_SESSION_THREAD_ID") == ""


def test_get_session_env_falls_back_to_os_environ(monkeypatch):
    """get_session_env should fall back to os.environ when contextvar is unset."""
    monkeypatch.setenv("TAKYON_SESSION_PLATFORM", "discord")

    # No contextvar set — should read from os.environ
    assert get_session_env("TAKYON_SESSION_PLATFORM") == "discord"

    # Now set a contextvar — should prefer it
    tokens = set_session_vars(platform="telegram")
    assert get_session_env("TAKYON_SESSION_PLATFORM") == "telegram"

    # After clear — should return "" (explicitly cleared), NOT fall back
    # to os.environ.  This is the fix for #10304: stale os.environ values
    # must not leak through after a gateway session is cleaned up.
    clear_session_vars(tokens)
    assert get_session_env("TAKYON_SESSION_PLATFORM") == ""


def test_get_session_env_default_when_nothing_set(monkeypatch):
    """get_session_env returns default when neither contextvar nor env is set."""
    monkeypatch.delenv("TAKYON_SESSION_PLATFORM", raising=False)

    assert get_session_env("TAKYON_SESSION_PLATFORM") == ""
    assert get_session_env("TAKYON_SESSION_PLATFORM", "fallback") == "fallback"


def test_set_session_env_handles_missing_optional_fields():
    """_set_session_env should handle None chat_name and thread_id gracefully."""
    runner = object.__new__(GatewayRunner)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_name=None,
        chat_type="private",
        thread_id=None,
    )
    context = SessionContext(source=source, connected_platforms=[], home_channels={})

    tokens = runner._set_session_env(context)

    assert get_session_env("TAKYON_SESSION_PLATFORM") == "telegram"
    assert get_session_env("TAKYON_SESSION_CHAT_ID") == "-1001"
    assert get_session_env("TAKYON_SESSION_CHAT_NAME") == ""
    assert get_session_env("TAKYON_SESSION_THREAD_ID") == ""

    runner._clear_session_env(tokens)


# ---------------------------------------------------------------------------
# SESSION_KEY contextvars tests
# ---------------------------------------------------------------------------


def test_session_key_set_via_contextvars(monkeypatch):
    """set_session_vars should set TAKYON_SESSION_KEY via contextvars."""
    monkeypatch.delenv("TAKYON_SESSION_KEY", raising=False)

    tokens = set_session_vars(
        platform="telegram",
        chat_id="-1001",
        session_key="tg:-1001:17585",
    )
    assert get_session_env("TAKYON_SESSION_KEY") == "tg:-1001:17585"

    clear_session_vars(tokens)
    assert get_session_env("TAKYON_SESSION_KEY") == ""


def test_session_key_falls_back_to_os_environ(monkeypatch):
    """get_session_env for SESSION_KEY should fall back to os.environ."""
    monkeypatch.setenv("TAKYON_SESSION_KEY", "env-session-123")

    # No contextvar set — should read from os.environ
    assert get_session_env("TAKYON_SESSION_KEY") == "env-session-123"

    # Set contextvar — should prefer it
    tokens = set_session_vars(session_key="ctx-session-456")
    assert get_session_env("TAKYON_SESSION_KEY") == "ctx-session-456"

    # After clear — should return "" (explicitly cleared), not os.environ (#10304)
    clear_session_vars(tokens)
    assert get_session_env("TAKYON_SESSION_KEY") == ""


def test_workspace_root_flows_into_takyon_store(monkeypatch, tmp_path):
    home = tmp_path / "home"
    scratch = tmp_path / "scratch-home"
    monkeypatch.delenv("TAKYON_SESSION_WORKSPACE_ROOT", raising=False)

    tokens = set_session_vars(workspace_root=str(scratch))
    try:
        store = TakyonStore(root=home)
        assert store.root == home.resolve()
        assert store._business_root("acme") == (scratch / "businesses" / "acme").resolve()
        assert not hasattr(store, "db_path")
        assert get_session_env("TAKYON_SESSION_WORKSPACE_ROOT") == str(scratch)
    finally:
        clear_session_vars(tokens)


def test_business_slug_contextvar_round_trips(monkeypatch):
    monkeypatch.delenv("TAKYON_SESSION_BUSINESS_SLUG", raising=False)

    tokens = set_session_vars(business_slug="acme")
    assert get_session_env("TAKYON_SESSION_BUSINESS_SLUG") == "acme"

    clear_session_vars(tokens)
    assert get_session_env("TAKYON_SESSION_BUSINESS_SLUG") == ""


def test_store_read_refreshes_remote_workspace_between_calls(monkeypatch, tmp_path, pg_store_dsn):
    bucket = tmp_path / "bucket"
    home = tmp_path / "home"
    seed = tmp_path / "seed"
    (seed / "research").mkdir(parents=True, exist_ok=True)
    (seed / "research" / "alpha.md").write_text("alpha\n")
    backend = storage.LocalStorageBackend(bucket)
    storage.sync_up(backend, "acme", seed)

    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(bucket))
    monkeypatch.setenv("DATABASE_URL", pg_store_dsn)
    monkeypatch.setenv("TAKYON_PLATFORM_OWNER_SUB", "auth0|session-env-read-refresh")

    store = TakyonStore(root=home, database_url=pg_store_dsn)
    store.seed_platform_owner()
    store.commit(
        scope="global",
        operations=[{"action": "business.upsert", "business": "acme", "name": "Acme", "mode": "test"}],
        idempotency_key="test:session-env-read-refresh:init",
        reason="test",
        actor="test",
    )

    first = store.read(scope="business:acme", query="files", path="research")
    assert {item["path"] for item in first["files"]} >= {"research/alpha.md"}

    delta = tmp_path / "delta"
    (delta / "research").mkdir(parents=True, exist_ok=True)
    (delta / "research" / "alpha.md").write_text("alpha\n")
    (delta / "research" / "beta.md").write_text("beta\n")
    storage.sync_up(backend, "acme", delta)

    second = store.read(scope="business:acme", query="files", path="research")
    assert {item["path"] for item in second["files"]} >= {"research/alpha.md", "research/beta.md"}


def test_store_syncs_scratch_writes_outward_during_isolated_run(monkeypatch, tmp_path, pg_store_dsn):
    bucket = tmp_path / "bucket"
    home = tmp_path / "home"
    scratch = tmp_path / "scratch-home"

    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(bucket))
    monkeypatch.setenv("DATABASE_URL", pg_store_dsn)
    monkeypatch.setenv("TAKYON_PLATFORM_OWNER_SUB", "auth0|session-env-scratch-sync")

    tokens = set_session_vars(workspace_root=str(scratch))
    try:
        writer = TakyonStore(root=home, database_url=pg_store_dsn)
        writer.seed_platform_owner()
        writer.commit(
            scope="global",
            operations=[{"action": "business.upsert", "business": "acme", "name": "Acme"}],
            idempotency_key="test:business-upsert",
        )
        writer.commit(
            scope="business:acme",
            operations=[
                {
                    "action": "artifact.write",
                    "path": "product/site/index.html",
                    "content": "<html>fresh</html>\n",
                }
            ],
            idempotency_key="test:artifact-write",
        )
    finally:
        clear_session_vars(tokens)

    reader = TakyonStore(root=home, database_url=pg_store_dsn)
    result = reader.read(scope="business:acme", query="file", path="product/site/index.html")
    assert result["content"] == "<html>fresh</html>\n"


def test_store_syncs_scratch_writes_outward_with_default_local_backend(monkeypatch, tmp_path, pg_store_dsn):
    home = tmp_path / "home"
    scratch = tmp_path / "scratch-home"

    monkeypatch.setattr(takyon_core, "load_takyon_env", lambda: [])
    monkeypatch.setenv("TAKYON_HOME", str(home))
    monkeypatch.delenv("TAKYON_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("TAKYON_STORAGE_LOCAL_DIR", raising=False)
    for key in (
        "SUPABASE_S3_ENDPOINT",
        "SUPABASE_S3_REGION",
        "SUPABASE_S3_ACCESS_KEY_ID",
        "SUPABASE_S3_SECRET_ACCESS_KEY",
        "TAKYON_STORAGE_BUCKET",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", pg_store_dsn)
    monkeypatch.setenv("TAKYON_PLATFORM_OWNER_SUB", "auth0|session-env-default-local")

    tokens = set_session_vars(workspace_root=str(scratch))
    try:
        writer = TakyonStore(root=home, database_url=pg_store_dsn)
        writer.seed_platform_owner()
        writer.commit(
            scope="global",
            operations=[{"action": "business.upsert", "business": "acme", "name": "Acme"}],
            idempotency_key="test:business-upsert-default-local",
        )
        writer.commit(
            scope="business:acme",
            operations=[
                {
                    "action": "artifact.write",
                    "path": "research/plan.md",
                    "content": "ship\n",
                }
            ],
            idempotency_key="test:artifact-write-default-local",
        )
    finally:
        clear_session_vars(tokens)

    backend = storage.LocalStorageBackend(home / "storage")
    resumed = tmp_path / "resumed"
    storage.sync_down(backend, "acme", resumed)
    assert (resumed / "research" / "plan.md").read_text() == "ship\n"

    reader = TakyonStore(root=home, database_url=pg_store_dsn)
    result = reader.read(scope="business:acme", query="file", path="research/plan.md")
    assert result["content"] == "ship\n"


def test_set_session_env_includes_session_key():
    """_set_session_env should propagate session_key from SessionContext."""
    runner = object.__new__(GatewayRunner)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_name="Group",
        chat_type="group",
        thread_id="17585",
    )
    context = SessionContext(
        source=source,
        connected_platforms=[],
        home_channels={},
        session_key="tg:-1001:17585",
    )

    # Capture baseline value before setting (may be non-empty from another
    # test in the same pytest-xdist worker sharing the context).
    tokens = runner._set_session_env(context)
    assert get_session_env("TAKYON_SESSION_KEY") == "tg:-1001:17585"
    runner._clear_session_env(tokens)
    # After clearing, the session key must not retain the value we just set.
    # The exact post-clear value depends on context propagation from other
    # tests, so only check that our value was removed, not what replaced it.
    assert get_session_env("TAKYON_SESSION_KEY") != "tg:-1001:17585"


def test_session_key_no_race_condition_with_contextvars(monkeypatch):
    """Prove contextvars isolates SESSION_KEY across concurrent async tasks.

    Two tasks set different session keys. With contextvars each task
    reads back its own value. With os.environ the second task would
    overwrite the first (the old bug).
    """
    monkeypatch.delenv("TAKYON_SESSION_KEY", raising=False)

    results = {}

    async def handler(key: str, delay: float):
        tokens = set_session_vars(session_key=key)
        try:
            await asyncio.sleep(delay)
            read_back = get_session_env("TAKYON_SESSION_KEY")
            results[key] = read_back
        finally:
            clear_session_vars(tokens)

    async def run():
        task_a = asyncio.create_task(handler("session-A", 0.15))
        await asyncio.sleep(0.05)
        task_b = asyncio.create_task(handler("session-B", 0.05))
        await asyncio.gather(task_a, task_b)

    asyncio.run(run())

    # Both tasks must read back their own session key
    assert results["session-A"] == "session-A", (
        f"Session A got '{results['session-A']}' instead of 'session-A' — race condition!"
    )
    assert results["session-B"] == "session-B", (
        f"Session B got '{results['session-B']}' instead of 'session-B' — race condition!"
    )


@pytest.mark.asyncio
async def test_run_in_executor_with_context_preserves_session_env(monkeypatch):
    """Gateway executor work should inherit session contextvars for tool routing."""
    runner = object.__new__(GatewayRunner)
    monkeypatch.delenv("TAKYON_SESSION_PLATFORM", raising=False)
    monkeypatch.delenv("TAKYON_SESSION_CHAT_ID", raising=False)
    monkeypatch.delenv("TAKYON_SESSION_THREAD_ID", raising=False)
    monkeypatch.delenv("TAKYON_SESSION_USER_ID", raising=False)

    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="2144471399",
        chat_type="dm",
        user_id="123456",
        user_name="alice",
        thread_id=None,
    )
    context = SessionContext(
        source=source,
        connected_platforms=[],
        home_channels={},
        session_key="agent:main:telegram:dm:2144471399",
    )

    tokens = runner._set_session_env(context)
    try:
        result = await runner._run_in_executor_with_context(
            lambda: {
                "platform": get_session_env("TAKYON_SESSION_PLATFORM"),
                "chat_id": get_session_env("TAKYON_SESSION_CHAT_ID"),
                "user_id": get_session_env("TAKYON_SESSION_USER_ID"),
                "session_key": get_session_env("TAKYON_SESSION_KEY"),
            }
        )
    finally:
        runner._clear_session_env(tokens)

    assert result == {
        "platform": "telegram",
        "chat_id": "2144471399",
        "user_id": "123456",
        "session_key": "agent:main:telegram:dm:2144471399",
    }


@pytest.mark.asyncio
async def test_run_in_executor_with_context_forwards_args():
    """_run_in_executor_with_context should forward *args to the callable."""
    runner = object.__new__(GatewayRunner)

    def add(a, b):
        return a + b

    result = await runner._run_in_executor_with_context(add, 3, 7)
    assert result == 10


@pytest.mark.asyncio
async def test_run_in_executor_with_context_propagates_exceptions():
    """Exceptions inside the executor should propagate to the caller."""
    runner = object.__new__(GatewayRunner)

    def blow_up():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await runner._run_in_executor_with_context(blow_up)
