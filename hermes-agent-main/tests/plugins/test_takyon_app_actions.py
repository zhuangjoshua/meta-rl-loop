from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from plugins.takyon import app_actions, core as takyon_core, storage as takyon_storage, worker as takyon_worker


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
    worker_contract = "\n".join(rail["worker_contract"])
    assert "api.openai.com" in worker_contract
    assert "`OPENAI_API_KEY`" in worker_contract
    assert "`@anthropic-ai/sdk`" in worker_contract
    assert "`next.config.*`" in worker_contract
    assert "Client code must not call `/generate` directly" in worker_contract


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


def test_summarize_action_invocations_supports_pg_mapping_rows(monkeypatch):
    class _Cursor:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class _Conn:
        def execute(self, query, params):
            assert "purpose = 'action_invoke'" in query
            assert params == ("mathflow", "/api/takyon/apps/mathflow/actions/sync")
            return _Cursor(
                {
                    "status": "completed",
                    "error": "",
                    "created_at": "2026-06-14T19:00:00+00:00",
                    "completed_at": "2026-06-14T19:01:00+00:00",
                }
            )

    monkeypatch.setattr(app_actions, "_is_pg_conn", lambda conn: True)

    summary = app_actions.summarize_action_invocations(
        _Conn(),
        "mathflow",
        [{"name": "sync", "trigger": "http"}],
    )

    assert summary == [
        {
            "name": "sync",
            "trigger": "http",
            "last_status": "ok",
            "last_invoked_at": "2026-06-14T19:01:00+00:00",
            "last_error": "",
        }
    ]


def test_get_or_create_service_principal_uses_pg_metadata_column(monkeypatch):
    class _Cursor:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    queries: list[str] = []

    class _Conn:
        def execute(self, sql, params):
            queries.append(sql)
            if "WHERE business_slug = ? AND email = ?" in sql:
                return _Cursor(None)
            if "WHERE business_slug = ? AND id = ?" in sql:
                return _Cursor(
                    {
                        "id": params[1],
                        "business_slug": params[0],
                        "email": f"scheduler@service.{params[0]}.takyon.invalid",
                        "tier": "service",
                    }
                )
            return _Cursor(None)

    class _Store:
        def _row_to_dict(self, row):
            return row

    monkeypatch.setattr(app_actions, "_is_pg_conn", lambda conn: True)

    principal = app_actions.get_or_create_service_principal(_Store(), _Conn(), "mathflow")

    assert principal["tier"] == "service"
    insert_sql = next(sql for sql in queries if sql.startswith("INSERT INTO app_users"))
    assert " metadata, " in insert_sql
    assert "metadata_json" not in insert_sql


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


def test_reserve_usage_pg_uses_leaf_conn_and_plan_limit(monkeypatch):
    from plugins.takyon import app_usage as takyon_app_usage

    class FakePGConn:
        pass

    raw_conn = object()
    captured: dict[str, object] = {}

    class _Store:
        @contextmanager
        def _connect(self):
            yield FakePGConn()

        @contextmanager
        def _leaf_conn(self, conn):
            assert isinstance(conn, FakePGConn)
            yield raw_conn

        def _app_leaves(self):
            return {
                "identity": SimpleNamespace(
                    get_app_user=lambda raw, business_slug, app_user_id: SimpleNamespace(
                        id=app_user_id,
                        tier="paid",
                    )
                ),
                "entitlements": SimpleNamespace(
                    get_active_entitlement=lambda raw, business_slug, app_user_id: SimpleNamespace(plan_key="monthly"),
                    get_plan_policy=lambda raw, business_slug, plan_key: SimpleNamespace(
                        tier="paid",
                        included_ai_budget_microusd=5_000_000,
                    ),
                    list_plan_policies=lambda raw, business_slug: [],
                ),
            }

    def _fake_reserve(conn, business_slug, **kwargs):
        captured["conn"] = conn
        captured["business_slug"] = business_slug
        captured.update(kwargs)

    monkeypatch.setattr(takyon_core, "_PGConn", FakePGConn)
    monkeypatch.setattr(takyon_app_usage, "reserve_usage", _fake_reserve)

    app_actions._reserve_usage(
        _Store(),
        "mathflow",
        reservation_key="idem_123",
        app_user_id="u_123",
        app_user_tier="paid",
        estimate_microusd=2_000,
        route="/api/takyon/apps/mathflow/actions/coach",
        metadata={"trigger": "http"},
    )

    assert captured["conn"] is raw_conn
    assert captured["business_slug"] == "mathflow"
    assert captured["user_monthly_limit_microusd"] == 5_000_000
    assert captured["app_user_tier"] == "paid"


def test_settle_and_release_usage_pg_use_leaf_conn(monkeypatch):
    from plugins.takyon import app_usage as takyon_app_usage

    class FakePGConn:
        pass

    raw_conn = object()
    calls: list[tuple[str, object, dict[str, object]]] = []

    class _Store:
        @contextmanager
        def _connect(self):
            yield FakePGConn()

        @contextmanager
        def _leaf_conn(self, conn):
            assert isinstance(conn, FakePGConn)
            yield raw_conn

    monkeypatch.setattr(takyon_core, "_PGConn", FakePGConn)
    monkeypatch.setattr(
        takyon_app_usage,
        "settle_usage",
        lambda conn, business_slug, reservation_key, **kwargs: calls.append(
            ("settle", conn, {"business_slug": business_slug, "reservation_key": reservation_key, **kwargs})
        ),
    )
    monkeypatch.setattr(
        takyon_app_usage,
        "release_usage",
        lambda conn, business_slug, reservation_key, **kwargs: calls.append(
            ("release", conn, {"business_slug": business_slug, "reservation_key": reservation_key, **kwargs})
        ),
    )

    app_actions._settle_usage(
        _Store(),
        "mathflow",
        reservation_key="idem_123",
        actual_microusd=2_000,
        metadata={"action": "coach"},
    )
    app_actions._release_usage(
        _Store(),
        "mathflow",
        reservation_key="idem_123",
        error="boom",
        metadata={"action": "coach"},
    )

    assert calls == [
        (
            "settle",
            raw_conn,
            {
                "business_slug": "mathflow",
                "reservation_key": "idem_123",
                "actual_cost_microusd": 2_000,
                "metadata": {"action": "coach"},
            },
        ),
        (
            "release",
            raw_conn,
            {
                "business_slug": "mathflow",
                "reservation_key": "idem_123",
                "error": "boom",
                "metadata": {"action": "coach"},
            },
        ),
    ]


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
        plans=None,
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


def test_surface_http_action_names_detects_referenced_file_backed_http_actions(tmp_path):
    base = tmp_path / "businesses" / "biz" / "product" / "site"
    (base / "src" / "screens").mkdir(parents=True)
    (base / "actions").mkdir(parents=True)
    (base / "src" / "screens" / "app-home.tsx").write_text(
        'const { run } = useActionRunner("coach-chat");\n', encoding="utf-8"
    )
    (base / "actions" / "coach-chat.ts").write_text(
        "export default async () => ({ ok: true });\n", encoding="utf-8"
    )

    class _Store:
        def _business_root(self, slug):
            return tmp_path / "businesses" / slug

    names = app_actions.surface_http_action_names(
        store=_Store(),
        business="biz",
        surface={"runtime_features": ["auth", "account"], "product_workflow": {}},
        source_path="product/site",
    )
    assert names == {"coach-chat"}


def test_surface_http_action_names_excludes_schedule_only_action_files(tmp_path):
    base = tmp_path / "businesses" / "biz" / "product" / "site"
    (base / "actions").mkdir(parents=True)
    (base / "actions" / "nightly-checkin.ts").write_text(
        "export default async () => ({ ok: true });\n", encoding="utf-8"
    )

    class _Store:
        def _business_root(self, slug):
            return tmp_path / "businesses" / slug

    names = app_actions.surface_http_action_names(
        store=_Store(),
        business="biz",
        surface={
            "runtime_features": ["auth", "account"],
            "product_workflow": {"actions": [{"name": "nightly-checkin", "trigger": "schedule"}]},
        },
        source_path="product/site",
    )
    assert names == set()


def test_product_surface_refresh_operations_persist_runtime_features_without_http_actions():
    operations = takyon_core._product_surface_refresh_operations(  # type: ignore[attr-defined]
        business="biz",
        surface_refresh={
            "status": "passed",
            "receipt_path": "metrics/receipts/product-surface/test.json",
            "source_path": "product/site",
            "runtime_features": ["auth", "account"],
            "publish": {
                "status": "published",
                "public_url": "https://biz.fourmanifold.com/",
                "publish_target": "https://biz.fourmanifold.com/",
            },
        },
        surface={
            "runtime_api_base": "/api/takyon/apps/biz",
            "runtime_features": ["auth", "account"],
            "routes": ["/", "/app"],
            "notes": "",
            "metadata": {},
        },
        publish_target="https://biz.fourmanifold.com/",
        publish_policy="publish_after_refresh",
        requested_publish_policy="publish_after_refresh",
        activate_on_success=True,
    )

    upsert = next(op for op in operations if op.get("action") == "app.surface.upsert")
    assert upsert["runtime_features"] == ["auth", "account"]


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


def test_operator_cache_sync_excludes_product_site_prefix(tmp_path, monkeypatch, pg_store_dsn):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("DATABASE_URL", pg_store_dsn)
    store = takyon_core.TakyonStore(tmp_path, database_url=pg_store_dsn)
    with store._connect() as conn:
        with conn:
            conn.execute(
                "INSERT INTO businesses (slug, name, mode, goal, status, work_focus, budget_json, created_at, updated_at) VALUES (?, ?, 'live', '', 'active', 'all', ?, ?, ?)",
                ("biz", "Biz", "{}", takyon_core._now(), takyon_core._now()),
            )
    workspace = store._business_root("biz", sync=False)
    (workspace / "research").mkdir(parents=True, exist_ok=True)
    (workspace / "research" / "strategy.md").write_text("# Strategy\n", encoding="utf-8")
    (workspace / "product" / "site" / "dist").mkdir(parents=True, exist_ok=True)
    (workspace / "product" / "site" / "dist" / "index.html").write_text("<main>build</main>\n", encoding="utf-8")

    status = store._sync_business_workspace_remote("biz")
    backend = store._workspace_storage_backend()
    manifest = takyon_storage.read_workspace_manifest(backend, "biz", store._business_head_revision("biz"))

    assert status == "synced"
    assert "research/strategy.md" in manifest["files"]
    assert "product/site/dist/index.html" not in manifest["files"]


def test_scoped_worker_sync_keeps_product_site_authority(tmp_path, monkeypatch, pg_store_dsn):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("DATABASE_URL", pg_store_dsn)
    store = takyon_core.TakyonStore(tmp_path, database_url=pg_store_dsn)
    scoped_root = tmp_path / "scratch-home"
    store._workspace_root_override = scoped_root
    store._workspace_base_revision["biz"] = 0
    with store._connect() as conn:
        with conn:
            conn.execute(
                "INSERT INTO businesses (slug, name, mode, goal, status, work_focus, budget_json, created_at, updated_at) VALUES (?, ?, 'live', '', 'active', 'all', ?, ?, ?)",
                ("biz", "Biz", "{}", takyon_core._now(), takyon_core._now()),
            )
    workspace = scoped_root / "businesses" / "biz"
    (workspace / "product" / "site").mkdir(parents=True, exist_ok=True)
    (workspace / "product" / "site" / "index.html").write_text("<h1>Live</h1>\n", encoding="utf-8")

    status = store._sync_business_workspace_remote("biz")
    backend = store._workspace_storage_backend()
    manifest = takyon_storage.read_workspace_manifest(backend, "biz", 1)

    assert status == "synced"
    assert "product/site/index.html" in manifest["files"]


def test_business_file_truth_metadata_labels_canonical_vs_working_product_source():
    cached = takyon_core._business_file_truth_metadata(
        "product/site/index.html",
        session_scoped=False,
    )
    working = takyon_core._business_file_truth_metadata(
        "product/site/index.html",
        session_scoped=True,
    )

    assert cached["truth_surface"] == "canonical"
    assert cached["proof_level"] == "mixed"
    assert "committed canonical workspace" in cached["truth_guidance"]

    assert working["truth_surface"] == "working"
    assert working["proof_level"] == "authoritative"
    assert "active session workspace" in working["proof_guidance"]


def test_recorded_live_truth_metadata_labels_intended_live_state():
    truth = takyon_core._recorded_live_truth_metadata(
        {
            "publish_status": "published",
            "public_url": "https://latexflow.fourmanifold.com/",
            "published_at": "2026-06-14T06:00:00+00:00",
        },
        business="latexflow",
    )

    assert truth["surface"] == "recorded_live"
    assert truth["live"] is True
    assert truth["publish_status"] == "published"
    assert truth["public_url"] == "https://latexflow.fourmanifold.com/"
    assert truth["probe"] == "unknown"


def test_recorded_live_truth_metadata_prefers_live_build_pointer_over_stale_status():
    truth = takyon_core._recorded_live_truth_metadata(
        {
            "publish_status": "blocked",
            "publish_target": "https://latexflow.fourmanifold.com/",
            "public_url": "",
            "published_at": "",
            "live_build_id": "build-123",
            "publish_blocker": "old timeout",
        },
        business="latexflow",
    )

    assert truth["surface"] == "recorded_live"
    assert truth["committed"] is True
    assert truth["publish_status"] == "published"
    assert truth["build_id"] == "build-123"
    assert truth["public_url"] == "https://latexflow.fourmanifold.com/"
