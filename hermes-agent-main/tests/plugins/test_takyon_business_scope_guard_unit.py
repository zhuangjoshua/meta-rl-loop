from __future__ import annotations

import json

from gateway.session_context import clear_session_vars, set_session_vars
from plugins.takyon import core


class _Store:
    def __init__(self, existing: set[str]):
        self.existing = existing
        self.commits: list[dict] = []

    def enforce_operator_business_access(self, business: str) -> None:
        if business not in self.existing:
            raise core.TakyonError(f"business:{business} does not exist")

    def commit(self, **kwargs):
        self.commits.append(kwargs)
        return {"success": True, "results": [{"business": kwargs["operations"][0]["business"]}]}


def test_model_facing_business_upsert_refuses_missing_business(monkeypatch):
    store = _Store(set())
    monkeypatch.setattr(core, "_store", lambda: store)

    result = json.loads(
        core.handle_business_upsert_business(
            {
                "business": "accidental",
                "name": "Accidental",
                "idempotency_key": "accidental-create",
            }
        )
    )

    assert result["success"] is False
    assert "business:accidental does not exist" in result["error"]
    assert store.commits == []


def test_model_facing_business_upsert_is_transactionally_update_only(monkeypatch):
    store = _Store({"ching"})
    monkeypatch.setattr(core, "_store", lambda: store)

    result = json.loads(
        core.handle_business_upsert_business(
            {
                "business": "ching",
                "name": "Ching Updated",
                "idempotency_key": "update-ching",
            }
        )
    )

    assert result["success"] is True
    assert store.commits[0]["scope"] == "business:ching"
    assert store.commits[0]["operations"][0]["require_existing"] is True


def test_model_facing_business_upsert_cannot_cross_current_scope(monkeypatch):
    store = _Store({"ching", "other"})
    monkeypatch.setattr(core, "_store", lambda: store)
    tokens = set_session_vars(business_slug="ching")
    try:
        result = json.loads(
            core.handle_business_upsert_business(
                {
                    "business": "other",
                    "name": "Other Changed",
                    "idempotency_key": "cross-scope-update",
                }
            )
        )
    finally:
        clear_session_vars(tokens)

    assert result["success"] is False
    assert "bound to the current session" in result["error"]
    assert store.commits == []
