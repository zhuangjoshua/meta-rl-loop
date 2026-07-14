from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from plugins.takyon import core


class _SqliteCommitStore(core.TakyonStore):
    """Small DB-API adapter exercising the real preclaim-aware TakyonStore.commit path."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._test_db = root / "preclaim.sqlite3"
        self.surface_contracts: dict[str, dict[str, Any]] = {}
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE idempotency_keys (
                    key TEXT PRIMARY KEY,
                    operation_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_slug TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._test_db)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _business_root(self, slug: str, *, sync: bool = True) -> Path:
        del sync
        return self.root / "businesses" / slug

    def read(self, **kwargs: Any) -> dict[str, Any]:
        scope = str(kwargs.get("scope") or "business:preclaim")
        business = scope.split(":", 1)[1].split("/", 1)[0]
        surface = self.surface_contracts.setdefault(
            business,
            {
                "source_path": "product/site",
                "publish_target": f"https://{business}.coscale.app/",
                "publish_policy": "publish_after_refresh",
                "runtime_features": [],
                "routes": ["/"],
                "metadata": {},
            },
        )
        return {
            "app": {
                "surface_contract": dict(surface),
                "plans": [],
            }
        }

    def _normalize_operation(self, conn, parsed_scope, op, *, principal=None):
        del conn, parsed_scope, principal
        return {**op, "business_slug": str(op.get("business") or "")}

    def _apply_operation(self, conn, parsed_scope, op, *, reason, actor):
        del parsed_scope, reason, actor
        row = conn.execute(
            "INSERT INTO events (business_slug, event_type, payload_json) VALUES (?, ?, ?) RETURNING id",
            (
                op["business_slug"],
                op["event_type"],
                json.dumps(op.get("payload") or {}, sort_keys=True),
            ),
        ).fetchone()
        return {"action": "event.record", "id": int(row["id"])}


def _prepare(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.delenv("TAKYON_OPERATOR_TASKS_VIA_WORKER", raising=False)
    store = _SqliteCommitStore(tmp_path)
    source_root = store._business_root("preclaim") / "product/site"
    source_root.mkdir(parents=True)
    source_file = source_root / "src.tsx"
    source_file.write_text("export const version = 'one';\n", encoding="utf-8")
    calls: list[dict[str, Any]] = []

    def finalize(**kwargs: Any) -> dict[str, Any]:
        calls.append(dict(kwargs))
        return {
            "status": "passed",
            "kind": "node_build",
            "source_path": kwargs["source_path"],
            "receipt_path": kwargs["receipt_path"],
            "runtime_features": [],
            "inventory": {},
            "publish": {
                "status": "published",
                "public_url": kwargs["publish_target"],
                "live_build_id": "build-one",
                "database_build_activated": True,
                "blocker": "",
            },
            "blocker": "",
        }

    def operations(**kwargs: Any) -> list[dict[str, Any]]:
        refresh = kwargs["surface_refresh"]
        return [
            {
                "action": "event.record",
                "business": kwargs["business"],
                "event_type": "product.surface.refresh.preclaim-test",
                "payload": {"receipt_path": refresh["receipt_path"]},
            }
        ]

    monkeypatch.setattr(core, "_store", lambda: store)
    monkeypatch.setattr(core, "_finalize_product_surface_refresh", finalize)
    monkeypatch.setattr(core, "_product_surface_refresh_operations", operations)
    return store, source_file, calls


def test_changed_source_reuse_fails_before_finalize(tmp_path, monkeypatch):
    _store, source_file, calls = _prepare(tmp_path, monkeypatch)
    args = {
        "business": "preclaim",
        "source_path": "product/site",
        "install": False,
        "idempotency_key": "changed-source",
    }
    assert json.loads(core.handle_business_refresh_product_surface(args))["success"] is True
    source_file.write_text("export const version = 'two';\n", encoding="utf-8")

    conflict = json.loads(core.handle_business_refresh_product_surface(args))

    assert conflict["success"] is False
    assert "already used for different operations" in conflict["error"]
    assert len(calls) == 1


def test_exact_retry_replays_full_result_without_finalize_or_commit(tmp_path, monkeypatch):
    store, _source_file, calls = _prepare(tmp_path, monkeypatch)
    args = {
        "business": "preclaim",
        "source_path": "product/site",
        "install": False,
        "idempotency_key": "exact-replay",
    }

    first = json.loads(core.handle_business_refresh_product_surface(args))
    replay = json.loads(core.handle_business_refresh_product_surface(args))

    assert replay == first
    assert len(calls) == 1
    with store._connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM events WHERE event_type = ?",
            ("product.surface.refresh.preclaim-test",),
        ).fetchone()["count"]
    assert int(count) == 1


def test_exact_retry_ignores_runtime_owned_post_publish_surface_state(tmp_path, monkeypatch):
    store, _source_file, calls = _prepare(tmp_path, monkeypatch)
    surface = store.read(scope="business:preclaim")["app"]["surface_contract"]
    surface["metadata"] = {"product_authored": {"theme": "violet"}}
    store.surface_contracts["preclaim"] = surface
    args = {
        "business": "preclaim",
        "source_path": "product/site",
        "install": False,
        "idempotency_key": "post-publish-replay",
    }

    first = json.loads(core.handle_business_refresh_product_surface(args))
    surface = store.surface_contracts["preclaim"]
    surface.update(
        {
            "status": "active",
            "public_url": "https://preclaim.coscale.app/",
            "publish_status": "published",
            "published_at": "2026-07-13T12:00:00+00:00",
            "live_build_id": "a" * 32,
        }
    )
    surface["metadata"] = {
        "product_authored": {"theme": "violet"},
        "takyon_publish": {"live_build_id": "a" * 32},
        "takyon_publish_last_attempt": {"status": "published"},
    }

    replay = json.loads(core.handle_business_refresh_product_surface(args))

    assert replay == first
    assert len(calls) == 1


def test_same_raw_key_is_independent_across_businesses(tmp_path, monkeypatch):
    store, _source_file, calls = _prepare(tmp_path, monkeypatch)
    second_root = store._business_root("preclaim-two") / "product/site"
    second_root.mkdir(parents=True)
    (second_root / "src.tsx").write_text("export const version = 'one';\n", encoding="utf-8")
    raw_key = "shared-human-step"

    first = json.loads(
        core.handle_business_refresh_product_surface(
            {
                "business": "preclaim",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": raw_key,
            }
        )
    )
    second = json.loads(
        core.handle_business_refresh_product_surface(
            {
                "business": "preclaim-two",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": raw_key,
            }
        )
    )

    assert first["success"] is True
    assert second["success"] is True
    assert len(calls) == 2
    first_key = core._product_refresh_idempotency_storage_key(raw_key, business="preclaim")
    second_key = core._product_refresh_idempotency_storage_key(raw_key, business="preclaim-two")
    assert first_key != second_key
    with store._connect() as conn:
        keys = {
            row["key"]
            for row in conn.execute(
                "SELECT key FROM idempotency_keys WHERE key IN (?, ?)",
                (first_key, second_key),
            ).fetchall()
        }
    assert keys == {first_key, second_key}


def test_legacy_product_refresh_key_fails_closed_before_finalize(tmp_path, monkeypatch):
    store, _source_file, calls = _prepare(tmp_path, monkeypatch)
    raw_key = "legacy-product-refresh"
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO idempotency_keys (key, operation_hash, result_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                raw_key,
                "f" * 64,
                json.dumps(
                    {
                        "success": True,
                        "scope": "business:preclaim",
                        "results": [
                            {
                                "action": "artifact.write",
                                "path": "metrics/receipts/product-surface/legacy.json",
                            }
                        ],
                    }
                ),
                "2026-07-13T12:00:00+00:00",
            ),
        )

    blocked = json.loads(
        core.handle_business_refresh_product_surface(
            {
                "business": "preclaim",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": raw_key,
            }
        )
    )

    assert blocked["success"] is False
    assert "live side effect will not be repeated" in blocked["error"]
    assert calls == []
    storage_key = core._product_refresh_idempotency_storage_key(raw_key, business="preclaim")
    with store._connect() as conn:
        scoped_count = conn.execute(
            "SELECT COUNT(*) AS count FROM idempotency_keys WHERE key = ?",
            (storage_key,),
        ).fetchone()["count"]
    assert int(scoped_count) == 0


def test_unrelated_legacy_raw_key_does_not_block_scoped_refresh(tmp_path, monkeypatch):
    store, _source_file, calls = _prepare(tmp_path, monkeypatch)
    raw_key = "legacy-unrelated-tool"
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO idempotency_keys (key, operation_hash, result_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                raw_key,
                "e" * 64,
                json.dumps(
                    {
                        "success": True,
                        "scope": "business:preclaim",
                        "results": [{"action": "business.focus.set"}],
                    }
                ),
                "2026-07-13T12:00:00+00:00",
            ),
        )

    result = json.loads(
        core.handle_business_refresh_product_surface(
            {
                "business": "preclaim",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": raw_key,
            }
        )
    )

    assert result["success"] is True
    assert len(calls) == 1


def test_lost_worker_claim_cannot_write_preclaim(tmp_path, monkeypatch):
    store, _source_file, calls = _prepare(tmp_path, monkeypatch)
    raw_key = "lost-worker"

    def reject_claim(_store, _operation):
        raise core.TakyonError("worker claim was lost")

    monkeypatch.setattr(core, "_assert_active_worker_claim", reject_claim)
    blocked = json.loads(
        core.handle_business_refresh_product_surface(
            {
                "business": "preclaim",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": raw_key,
            }
        )
    )

    assert blocked["success"] is False
    assert "worker claim was lost" in blocked["error"]
    assert calls == []
    storage_key = core._product_refresh_idempotency_storage_key(raw_key, business="preclaim")
    with store._connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM idempotency_keys WHERE key = ?",
            (storage_key,),
        ).fetchone()["count"]
    assert int(count) == 0


def test_nested_product_owned_takyon_directory_is_collision_bound(tmp_path, monkeypatch):
    store, _source_file, calls = _prepare(tmp_path, monkeypatch)
    nested = store._business_root("preclaim") / "product/site/src/_takyon/input.ts"
    nested.parent.mkdir(parents=True)
    nested.write_text("export const input = 'one';\n", encoding="utf-8")
    args = {
        "business": "preclaim",
        "source_path": "product/site",
        "install": False,
        "idempotency_key": "nested-takyon-input",
    }
    assert json.loads(core.handle_business_refresh_product_surface(args))["success"] is True
    nested.write_text("export const input = 'two';\n", encoding="utf-8")

    blocked = json.loads(core.handle_business_refresh_product_surface(args))

    assert blocked["success"] is False
    assert "already used for different operations" in blocked["error"]
    assert len(calls) == 1


def test_changed_argument_reuse_fails_before_finalize(tmp_path, monkeypatch):
    _store, _source_file, calls = _prepare(tmp_path, monkeypatch)
    args = {
        "business": "preclaim",
        "source_path": "product/site",
        "install": False,
        "timeout_seconds": 300,
        "idempotency_key": "changed-argument",
    }
    assert json.loads(core.handle_business_refresh_product_surface(args))["success"] is True

    conflict = json.loads(
        core.handle_business_refresh_product_surface({**args, "timeout_seconds": 301})
    )

    assert conflict["success"] is False
    assert "already used for different operations" in conflict["error"]
    assert len(calls) == 1


def test_pending_claim_retry_fails_closed_without_finalize(tmp_path, monkeypatch):
    store, _source_file, calls = _prepare(tmp_path, monkeypatch)
    args = {
        "business": "preclaim",
        "source_path": "product/site",
        "install": False,
        "idempotency_key": "pending-claim",
    }
    app = store.read()["app"]
    surface = app["surface_contract"]
    source_root = store._business_root("preclaim") / "product/site"
    claim, replay = core._acquire_idempotency_preclaim(
        store,
        idempotency_key=core._product_refresh_idempotency_storage_key(
            args["idempotency_key"],
            business="preclaim",
        ),
        kind="business_refresh_product_surface",
        operation_identity=core._product_refresh_operation_identity(
            business="preclaim",
            surface=surface,
            plans=app["plans"],
            source_path="product/site",
            publish_target="https://preclaim.coscale.app/",
            requested_publish_policy="publish_after_refresh",
            publish_policy="publish_after_refresh",
            install=False,
            timeout_seconds=300,
            activate_on_success=True,
            reason="product surface publication",
            actor="agent",
        ),
        current_state_identity=core._product_refresh_source_state_identity(source_root),
    )
    assert claim is not None
    assert replay is None

    pending_retry = json.loads(core.handle_business_refresh_product_surface(args))

    assert pending_retry["success"] is False
    assert "already claimed by an in-progress operation" in pending_retry["error"]
    assert len(calls) == 0
