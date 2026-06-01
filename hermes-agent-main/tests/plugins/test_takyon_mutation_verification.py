from __future__ import annotations

import json
from pathlib import Path

from gateway.session_context import clear_session_vars, set_session_vars
from plugins.takyon import core as takyon_core
from plugins.takyon.core import handle_business_write_file


class _FakeStore:
    def __init__(self, root: Path):
        self.root = root

    def _business_root(self, slug: str) -> Path:
        return self.root / "businesses" / slug

    def _resolve_business_file(
        self,
        slug: str,
        rel: str,
        *,
        require_output_root: bool = False,
        field: str = "business path",
    ) -> Path:
        root = self._business_root(slug)
        relative = (
            takyon_core._canonical_business_output_relpath(rel, field=field)
            if require_output_root
            else takyon_core._canonical_business_relpath(rel)
        )
        path = (root / relative).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if root.resolve() not in (path, *path.parents):
            raise takyon_core.TakyonError("path escaped business root")
        return path

    def commit(self, *, scope: str, operations: list[dict], idempotency_key: str, reason: str, actor: str):
        op = dict(operations[0])
        slug = str(op.get("business") or "").strip()
        action = str(op.get("action") or "")
        if action != "artifact.write":
            raise AssertionError(f"unexpected action: {action}")
        file_path = self._resolve_business_file(slug, str(op.get("path") or ""))
        content = str(op.get("content") or "")
        mode = str(op.get("mode") or "replace")
        if mode == "append" and file_path.exists():
            content = file_path.read_text(encoding="utf-8", errors="replace") + content
        takyon_core._atomic_write_text(file_path, content)
        return {"action": action, "business": slug, "path": str(file_path.relative_to(self._business_root(slug)))}


def test_business_write_file_returns_verified_postcondition(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = _FakeStore(tmp_path)
    monkeypatch.setattr(takyon_core, "_store", lambda: store)

    tokens = set_session_vars(business_slug="alpha")
    try:
        wrote = json.loads(
            handle_business_write_file(
                {
                    "path": "product/site/index.html",
                    "content": "<h1>Alpha</h1>\n",
                    "idempotency_key": "verified-write",
                }
            )
        )
        assert wrote["success"] is True
        assert wrote["verification"]["verified"] is True
        assert wrote["path"] == "product/site/index.html"
    finally:
        clear_session_vars(tokens)


def test_business_write_file_fails_when_postcondition_does_not_land(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = _FakeStore(tmp_path)
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    file_path = store._business_root("alpha") / "product" / "site" / "index.html"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("<h1>Old</h1>\n", encoding="utf-8")

    monkeypatch.setattr(takyon_core, "_atomic_write_text", lambda path, content: None)

    tokens = set_session_vars(business_slug="alpha")
    try:
        wrote = json.loads(
            handle_business_write_file(
                {
                    "path": "product/site/index.html",
                    "content": "<h1>New</h1>\n",
                    "idempotency_key": "failed-write",
                }
            )
        )
        assert wrote["success"] is False
        assert "postcondition verification failed" in str(wrote.get("error") or "")
        assert wrote["verification"]["verified"] is False
    finally:
        clear_session_vars(tokens)


def test_business_write_file_reuses_one_store_for_read_write_verify(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = _FakeStore(tmp_path)
    calls = {"count": 0}

    def _single_store():
        calls["count"] += 1
        if calls["count"] > 1:
            raise AssertionError("unexpected extra _store() call")
        return store

    monkeypatch.setattr(takyon_core, "_store", _single_store)

    tokens = set_session_vars(business_slug="alpha")
    try:
        wrote = json.loads(
            handle_business_write_file(
                {
                    "path": "product/site/index.html",
                    "content": "<h1>Only once</h1>\n",
                    "idempotency_key": "single-store-write",
                }
            )
        )
        assert wrote["success"] is True
        assert calls["count"] == 1
        assert store._resolve_business_file("alpha", "product/site/index.html").read_text(encoding="utf-8") == "<h1>Only once</h1>\n"
    finally:
        clear_session_vars(tokens)
