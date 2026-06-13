from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from plugins.takyon import app_actions, core as takyon_core, worker as takyon_worker


class _SQLiteStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @contextmanager
    def _connect(self):
        yield self._conn


def test_actions_runtime_registry_entry_is_canonical():
    rail = takyon_core.PRODUCT_RUNTIME_RAILS["actions"]

    assert rail["tools"] == ["business_invoke_app_action"]
    assert rail["endpoints"] == [("POST", "actions/<name>")]
    assert takyon_core._RUNTIME_FEATURE_DEPENDENCIES["actions"] == ("auth", "account")
    assert "actions" in takyon_core._RUNTIME_FEATURE_ORDER


def test_validate_action_contract_does_not_require_actions_rail():
    app_actions.validate_action_contract(
        specs=[{"name": "sync", "trigger": "http"}],
        outbound_hosts=[],
        runtime_features=["auth", "account"],
    )


def test_validate_action_contract_allows_actions_rail_before_named_actions_exist():
    app_actions.validate_action_contract(
        specs=[],
        outbound_hosts=[],
        runtime_features=["auth", "account", "actions"],
    )


def test_normalize_action_specs_maps_user_trigger_to_http():
    specs = app_actions.normalize_action_specs(
        [{"name": "generate-routine", "trigger": "user", "description": "Build the routine"}]
    )

    assert specs == [
        {"name": "generate-routine", "trigger": "http", "description": "Build the routine"}
    ]
    app_actions.validate_action_contract(
        specs=specs,
        outbound_hosts=[],
        runtime_features=["auth", "account", "actions"],
    )


def test_normalize_action_specs_defaults_missing_trigger_to_http_or_schedule():
    specs = app_actions.normalize_action_specs(
        [
            {"name": "generate-routine", "description": "Build the routine"},
            {"name": "nightly-checkin", "schedule": "0 * * * *"},
        ]
    )

    assert specs == [
        {"name": "generate-routine", "trigger": "http", "description": "Build the routine"},
        {"name": "nightly-checkin", "trigger": "schedule", "schedule": "0 * * * *"},
    ]
    app_actions.validate_action_contract(
        specs=specs,
        outbound_hosts=[],
        runtime_features=["auth", "account", "actions"],
    )


def test_summarize_action_invocations_reports_ok_failed_and_never():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE app_usage_events ("
        "id TEXT PRIMARY KEY, "
        "business_slug TEXT NOT NULL, "
        "app_user_id TEXT, "
        "app_user_tier TEXT, "
        "purpose TEXT NOT NULL, "
        "route TEXT NOT NULL, "
        "status TEXT NOT NULL, "
        "estimated_cost_microusd INTEGER NOT NULL DEFAULT 0, "
        "actual_cost_microusd INTEGER NOT NULL DEFAULT 0, "
        "metadata_json TEXT, "
        "error TEXT, "
        "created_at TEXT NOT NULL, "
        "completed_at TEXT"
        ")"
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO app_usage_events (id, business_slug, purpose, route, status, estimated_cost_microusd, actual_cost_microusd, metadata_json, error, created_at, completed_at) "
        "VALUES (?, ?, 'action_invoke', ?, 'completed', 2000, 2000, ?, NULL, ?, ?)",
        (
            "evt_ok",
            "mathflow",
            "/api/takyon/apps/mathflow/actions/sync",
            json.dumps({"action": "sync"}),
            now,
            now,
        ),
    )
    conn.execute(
        "INSERT INTO app_usage_events (id, business_slug, purpose, route, status, estimated_cost_microusd, actual_cost_microusd, metadata_json, error, created_at, completed_at) "
        "VALUES (?, ?, 'action_invoke', ?, 'failed', 2000, 0, ?, ?, ?, ?)",
        (
            "evt_failed",
            "mathflow",
            "/api/takyon/apps/mathflow/actions/nightly",
            json.dumps({"action": "nightly"}),
            "boom",
            now,
            now,
        ),
    )

    summary = app_actions.summarize_action_invocations(
        conn,
        "mathflow",
        [
            {"name": "sync", "trigger": "http"},
            {"name": "nightly", "trigger": "schedule", "schedule": "0 * * * *"},
            {"name": "draft", "trigger": "http"},
        ],
    )

    assert summary == [
        {
            "name": "sync",
            "trigger": "http",
            "last_status": "ok",
            "last_invoked_at": now,
            "last_error": "",
        },
        {
            "name": "nightly",
            "trigger": "schedule",
            "last_status": "failed",
            "last_invoked_at": now,
            "last_error": "boom",
        },
        {
            "name": "draft",
            "trigger": "http",
            "last_status": "never",
            "last_invoked_at": "",
            "last_error": "",
        },
    ]


def test_dispatch_due_action_schedules_advances_sqlite_cursor():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE app_action_schedules ("
        "business_slug TEXT NOT NULL, "
        "action_name TEXT NOT NULL, "
        "cron_schedule TEXT NOT NULL, "
        "enabled INTEGER NOT NULL, "
        "next_run_at TEXT NOT NULL, "
        "last_run_at TEXT, "
        "last_status TEXT, "
        "last_error TEXT, "
        "created_at TEXT, "
        "updated_at TEXT, "
        "PRIMARY KEY (business_slug, action_name)"
        ")"
    )
    now = datetime.now(timezone.utc)
    due_at = (now - timedelta(minutes=1)).isoformat()
    conn.execute(
        "INSERT INTO app_action_schedules (business_slug, action_name, cron_schedule, enabled, next_run_at, created_at, updated_at) "
        "VALUES (?, ?, ?, 1, ?, ?, ?)",
        ("mathflow", "sync", "*/30 * * * *", due_at, due_at, due_at),
    )

    enqueued: list[dict[str, str]] = []
    count = app_actions.dispatch_due_action_schedules(
        _SQLiteStore(conn),
        now,
        enqueued.append,
    )

    updated = conn.execute(
        "SELECT next_run_at FROM app_action_schedules WHERE business_slug = ? AND action_name = ?",
        ("mathflow", "sync"),
    ).fetchone()

    assert count == 1
    assert enqueued == [
        {
            "business_slug": "mathflow",
            "action_name": "sync",
            "window_key": due_at and f"action-sched:mathflow:sync:{datetime.fromisoformat(due_at).strftime('%Y%m%d%H%M')}",
        }
    ]
    assert datetime.fromisoformat(updated["next_run_at"]) > now


def test_handle_business_invoke_app_action_reaches_runner_without_actions_declared(monkeypatch):
    class _Store:
        @contextmanager
        def _connect(self):
            yield object()

        def _app_surface_contract(self, conn, business):
            assert business == "mathflow"
            return {"runtime_features": ["auth", "account"]}

    captured: dict[str, object] = {}

    def _fake_invoke(*args, **kwargs):
        captured.update(kwargs)
        return {"success": True, "result": {"ok": True}}

    monkeypatch.setattr(takyon_core, "_store", lambda: _Store())
    monkeypatch.setattr(
        takyon_core,
        "_resolve_sqlite_app_user",
        lambda conn, business, session_token=None: {"id": "u1", "email": "user@example.com", "tier": "free", "status": "active"},
    )
    monkeypatch.setattr(app_actions, "invoke_action", _fake_invoke)

    payload = json.loads(
        takyon_core.handle_business_invoke_app_action(
            {
                "business": "mathflow",
                "action": "sync",
                "session_token": "sess_123",
                "idempotency_key": "idem_123",
            }
        )
    )

    assert payload["success"] is True
    assert captured["business_slug"] == "mathflow"
    assert captured["action_name"] == "sync"


def test_handle_business_invoke_app_action_rejects_during_bootstrap_before_runner(monkeypatch):
    def _unexpected_store():
        raise AssertionError("store should not be touched during ceo_bootstrap refusal")

    monkeypatch.setattr(takyon_core, "_store", _unexpected_store)

    with takyon_core._bound_operator_task_context(task_kind="ceo_bootstrap"):
        payload = json.loads(
            takyon_core.handle_business_invoke_app_action(
                {
                    "business": "mathflow",
                    "action": "sync",
                    "session_token": "sess_123",
                    "idempotency_key": "idem_123",
                }
            )
        )

    assert payload["success"] is False
    assert "unavailable during ceo_bootstrap" in payload["error"]


def test_finalize_product_surface_refresh_includes_action_invocation_summary(tmp_path, monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE app_usage_events ("
        "id TEXT PRIMARY KEY, "
        "business_slug TEXT NOT NULL, "
        "app_user_id TEXT, "
        "app_user_tier TEXT, "
        "purpose TEXT NOT NULL, "
        "route TEXT NOT NULL, "
        "status TEXT NOT NULL, "
        "estimated_cost_microusd INTEGER NOT NULL DEFAULT 0, "
        "actual_cost_microusd INTEGER NOT NULL DEFAULT 0, "
        "metadata_json TEXT, "
        "error TEXT, "
        "created_at TEXT NOT NULL, "
        "completed_at TEXT"
        ")"
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO app_usage_events (id, business_slug, purpose, route, status, estimated_cost_microusd, actual_cost_microusd, metadata_json, error, created_at, completed_at) "
        "VALUES (?, ?, 'action_invoke', ?, 'completed', 2000, 2000, ?, NULL, ?, ?)",
        (
            "evt_ok",
            "mathflow",
            "/api/takyon/apps/mathflow/actions/sync",
            json.dumps({"action": "sync"}),
            now,
            now,
        ),
    )

    class _Store:
        def _business_root(self, slug):
            return tmp_path / "businesses" / slug

        @contextmanager
        def _connect(self):
            yield conn

    monkeypatch.setattr(
        takyon_core,
        "_refresh_product_surface_path",
        lambda *args, **kwargs: {
            "status": "passed",
            "source_path": "product/site",
            "inventory": {"status": "collected"},
        },
    )
    monkeypatch.setattr(
        takyon_core,
        "_publish_product_surface_path",
        lambda **kwargs: {
            "status": "published",
            "public_url": "https://mathflow.fourmanifold.com/",
            "publish_target": "https://mathflow.fourmanifold.com/",
            "publish_source_path": "product/site",
            "publish_mode": "copy",
        },
    )
    monkeypatch.setattr(app_actions, "action_refresh_blocker", lambda **kwargs: "")

    result = takyon_core._finalize_product_surface_refresh(  # type: ignore[attr-defined]
        store=_Store(),
        business="mathflow",
        surface={
            "runtime_features": ["auth", "account", "actions"],
            "metadata": {
                "product_workflow": {
                    "actions": [
                        {"name": "sync", "trigger": "http"},
                        {"name": "draft", "trigger": "http"},
                    ]
                }
            },
        },
        source_path="product/site",
        publish_target="https://mathflow.fourmanifold.com/",
        requested_publish_policy="publish_after_refresh",
        publish_policy="publish_after_refresh",
        install=False,
        timeout_seconds=60,
        receipt_path="metrics/receipts/product-surface/test.json",
        refresh_source="test",
    )

    assert result["action_invocations"] == [
        {
            "name": "sync",
            "trigger": "http",
            "last_status": "ok",
            "last_invoked_at": now,
            "last_error": "",
        },
        {
            "name": "draft",
            "trigger": "http",
            "last_status": "never",
            "last_invoked_at": "",
            "last_error": "",
        },
    ]


def test_product_surface_evidence_and_surface_md_include_action_invocation_statuses(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "mathflow"
    (business_root / "product").mkdir(parents=True)

    surface = {
        "status": "draft",
        "source_path": "product/docs",
        "runtime_features": ["auth", "account", "actions"],
        "metadata": {
            "subuser_app": {
                "app_mode": "ai_tool",
                "subscription_style": "monthly",
                "api_mode": "shared_runtime",
            },
            "customer_experience": {},
            "product_workflow": {
                "actions": [
                    {"name": "sync", "trigger": "http"},
                    {"name": "nightly", "trigger": "schedule", "schedule": "0 * * * *"},
                    {"name": "draft", "trigger": "http"},
                ],
                "outbound_hosts": ["api.example.com"],
            },
        },
    }
    evidence = {
        "inventory": {},
        "operational_facts": {},
        "action_invocations": [
            {
                "name": "sync",
                "trigger": "http",
                "last_status": "ok",
                "last_invoked_at": "2026-06-12T19:54:22+00:00",
                "last_error": "",
            },
            {
                "name": "nightly",
                "trigger": "schedule",
                "last_status": "failed",
                "last_invoked_at": "2026-06-12T19:55:22+00:00",
                "last_error": "boom",
            },
            {
                "name": "draft",
                "trigger": "http",
                "last_status": "never",
                "last_invoked_at": "",
                "last_error": "",
            },
        ],
        "local_continuable_work": [],
    }

    class _SurfaceStore:
        def _business_root(self, slug):
            assert slug == "mathflow"
            return business_root

        def _app_surface_contract(self, conn, slug):
            assert slug == "mathflow"
            return surface

        def _product_surface_evidence(self, conn, slug, provided_surface=None):
            assert slug == "mathflow"
            assert provided_surface == surface
            return evidence

    takyon_core.TakyonStore._rewrite_app_files(  # type: ignore[misc]
        _SurfaceStore(),
        sqlite3.connect(":memory:"),
        "mathflow",
    )

    surface_md = (business_root / "product" / "surface.md").read_text(encoding="utf-8")
    assert "Action invocation status: sync=ok, nightly=failed (boom), draft=never" in surface_md


def test_product_action_handler_executes_shared_executor(monkeypatch):
    calls: list[dict[str, str]] = []

    def _fake_store():
        return object()

    def _fake_execute(store, *, business_slug, action_name, window_key):
        calls.append(
            {
                "business_slug": business_slug,
                "action_name": action_name,
                "window_key": window_key,
            }
        )
        return {"success": True, "action": action_name}

    monkeypatch.setattr("plugins.takyon.core._store", _fake_store)
    monkeypatch.setattr(app_actions, "execute_scheduled_action", _fake_execute)

    result = takyon_worker.product_action_handler(
        SimpleNamespace(
            business_slug="mathflow",
            payload={"action": "sync", "window_key": "sched_123"},
            idempotency_key="sched_123",
        )
    )

    assert calls == [
        {
            "business_slug": "mathflow",
            "action_name": "sync",
            "window_key": "sched_123",
        }
    ]
    assert result.result == {"success": True, "action": "sync"}
    assert result.actual_cost_cents == 0


@pytest.mark.skipif(shutil.which("deno") is None, reason="deno is not installed")
def test_run_action_subprocess_executes_local_deno_action(tmp_path):
    action_path = tmp_path / "sum.ts"
    action_path.write_text(
        "export default async function (payload, ctx) {\n"
        "  return { total: Number(payload.a || 0) + Number(payload.b || 0), trigger: ctx.trigger, base: ctx.base_url };\n"
        "}\n",
        encoding="utf-8",
    )

    result, run = app_actions._run_action_subprocess(
        action_path=action_path,
        base=app_actions.RailsBase(
            origin="http://127.0.0.1:9119",
            hostport="127.0.0.1:9119",
        ),
        outbound_hosts=[],
        request={
            "payload": {"a": 2, "b": 3},
            "ctx": {
                "base_url": "http://127.0.0.1:9119/api/takyon/apps/mathflow",
                "session_token": "sess_123",
                "business": "mathflow",
                "trigger": "http",
                "principal": {"kind": "session", "id": "u_123", "email": "user@example.com"},
            },
        },
        timeout_seconds=30,
        cpu_quota_percent=50,
        memory_max_mb=256,
    )

    assert result == {
        "total": 5,
        "trigger": "http",
        "base": "http://127.0.0.1:9119/api/takyon/apps/mathflow",
    }
    assert run["timeout_seconds"] == 30
    assert run["isolation"] in {"subprocess", "systemd-scope"}


# --- Fresh-run regression fixes (latexflowfreshtrace) ---

def test_validator_allows_actions_in_features_without_product_workflow():
    """Build-first authoring: selecting `actions` may precede final action declaration."""
    takyon_core._validate_product_workflow_contract(  # type: ignore[attr-defined]
        surface={"metadata": {}},
        runtime_features=["auth", "account", "actions"],
        product_workflow=None,
    )


def test_validator_allows_actions_with_declared_spec():
    """Fix #1: a coherent actions contract (rail + spec) passes."""
    takyon_core._validate_product_workflow_contract(  # type: ignore[attr-defined]
        surface={"metadata": {}},
        runtime_features=["auth", "account", "actions"],
        product_workflow={"actions": [{"name": "translate", "trigger": "http"}]},
    )


def test_validator_allows_actions_spec_without_declaring_actions_rail():
    """The simplified authoring path no longer blocks specs on a missing actions rail."""
    assert (
        takyon_core._validate_product_workflow_contract(  # type: ignore[attr-defined]
            surface={"metadata": {}},
            runtime_features=["auth", "account"],
            product_workflow={"actions": [{"name": "translate", "trigger": "http"}]},
        )
        is None
    )


def test_action_blocker_flags_missing_ui_action_file(tmp_path):
    """UI invoking an action with no backing file is a blocker."""
    site = tmp_path / "businesses" / "biz" / "product" / "site" / "src" / "screens"
    site.mkdir(parents=True)
    (site / "app-home.tsx").write_text(
        'const { run } = useActionRunner("translate");\n', encoding="utf-8"
    )

    class _Store:
        def _business_root(self, slug):
            return tmp_path / "businesses" / slug

    blocker = app_actions.action_refresh_blocker(
        store=_Store(),
        business="biz",
        surface={"runtime_features": ["auth", "account"], "product_workflow": {}},
        source_path="product/site",
    )
    assert "product UI invokes action `translate`" in blocker
    assert "product/site/actions/translate.ts does not exist" in blocker


def test_action_blocker_passes_when_ui_call_matches_declared_spec_and_file(tmp_path, monkeypatch):
    """Fix #2: UI call + declared spec + matching file is coherent (no blocker)."""
    base = tmp_path / "businesses" / "biz" / "product" / "site"
    (base / "src" / "screens").mkdir(parents=True)
    (base / "actions").mkdir(parents=True)
    (base / "src" / "screens" / "app-home.tsx").write_text(
        'const { run } = useActionRunner("translate");\n', encoding="utf-8"
    )
    (base / "actions" / "translate.ts").write_text(
        "export default async (payload, ctx) => ({ ok: true });\n", encoding="utf-8"
    )
    monkeypatch.setattr(app_actions.shutil, "which", lambda name: "/usr/bin/deno")

    class _Store:
        def _business_root(self, slug):
            return tmp_path / "businesses" / slug

    blocker = app_actions.action_refresh_blocker(
        store=_Store(),
        business="biz",
        surface={
            "runtime_features": ["auth", "account", "actions"],
            "product_workflow": {"actions": [{"name": "translate", "trigger": "http"}]},
        },
        source_path="product/site",
    )
    assert blocker == ""


def test_action_blocker_allows_actions_rail_without_declared_actions_when_source_has_no_action_usage(tmp_path, monkeypatch):
    """Build-first authoring: the rail alone is not a blocker until the product actually uses it."""
    base = tmp_path / "businesses" / "biz" / "product" / "site"
    (base / "src" / "screens").mkdir(parents=True)
    (base / "src" / "screens" / "app-home.tsx").write_text(
        "export default function AppHome() { return <main>Longer AI</main>; }\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app_actions.shutil, "which", lambda name: "/usr/bin/deno")

    class _Store:
        def _business_root(self, slug):
            return tmp_path / "businesses" / slug

    blocker = app_actions.action_refresh_blocker(
        store=_Store(),
        business="biz",
        surface={"runtime_features": ["auth", "account", "actions"], "product_workflow": {}},
        source_path="product/site",
    )
    assert blocker == ""


def test_sync_status_skipped_disallowed_is_truthful(monkeypatch):
    """Fix #3: a disallowed remote sync reports skipped_disallowed, not silent success."""
    monkeypatch.setattr(takyon_core, "_remote_workspace_sync_allowed", lambda name: False)

    class _Store:
        def _workspace_storage_backend(self):
            return SimpleNamespace(name="supabase_s3")

    status = takyon_core.TakyonStore._sync_business_workspace_remote(_Store(), "biz")
    assert status == "skipped_disallowed"


def test_sync_status_no_backend_is_truthful():
    """Fix #3: an unsupported backend reports skipped_no_backend, not synced."""

    class _Store:
        def _workspace_storage_backend(self):
            return SimpleNamespace(name="memory")

    status = takyon_core.TakyonStore._sync_business_workspace_remote(_Store(), "biz")
    assert status == "skipped_no_backend"
