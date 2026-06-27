from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


class _FakeDbError(Exception):
    sqlstate = "42501"


class _Row:
    def __init__(self, values):
        self._values = tuple(values)

    def __getitem__(self, index):
        return self._values[index]


class _Result:
    def __init__(self, values=(1,)):
        self._row = _Row(values)

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, *, plane: str):
        self.plane = plane
        self.closed = False
        self.commits = 0
        self.rollbacks = 0
        self.sql: list[str] = []

    def execute(self, sql, *args, **kwargs):
        text = str(sql).strip().lower()
        self.sql.append(text)
        if text.startswith("select set_config"):
            return _Result(("",))
        if text == "select takyon_rls_bypass()":
            return _Result((False,))
        if text.startswith("reset role"):
            return _Result()
        if text.startswith("set role"):
            raise _FakeDbError("permission denied to set role")
        if self.plane == "app":
            if text.startswith("select 1 from businesses"):
                raise _FakeDbError("permission denied for table businesses")
            if text.startswith("select 1 from app_users"):
                raise _FakeDbError("permission denied for table app_users")
            if text.startswith("select 1 from app_sessions"):
                raise _FakeDbError("permission denied for table app_sessions")
            if text.startswith("insert into app_usage_events"):
                raise _FakeDbError("permission denied for table app_usage_events")
            if text.startswith("insert into app_entitlements"):
                raise _FakeDbError("permission denied for table app_entitlements")
            if text.startswith("insert into app_revenue_events"):
                raise _FakeDbError("permission denied for table app_revenue_events")
        if self.plane == "operator":
            if text.startswith("insert into "):
                raise _FakeDbError("permission denied for money table")
            return _Result()
        if self.plane == "safebox":
            return _Result()
        return _Result()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "probe_db_authority_split.py"
    spec = importlib.util.spec_from_file_location("_probe_db_authority_split_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_runtime(monkeypatch, module, *, conn: _FakeConn, role: str):
    monkeypatch.setattr(module, "resolve_database_url", lambda explicit_dsn=None, plane=None: "postgresql://redacted")
    monkeypatch.setattr(module, "configure_takyon_pg_session", lambda conn, *, bypass: None)
    monkeypatch.setattr(module, "assert_takyon_pg_role", lambda conn, plane: (role, role))
    monkeypatch.setattr(module, "current_takyon_pg_roles", lambda conn: (role, role))
    monkeypatch.setattr(
        module,
        "psycopg",
        SimpleNamespace(connect=lambda *args, **kwargs: conn),
    )


def test_app_probe_checks_no_raw_user_session_or_money_access(monkeypatch):
    module = _load_module()
    conn = _FakeConn(plane="app")
    _patch_runtime(monkeypatch, module, conn=conn, role="takyon_app_runtime")

    result = module.probe_plane("app")

    assert result["ok"] is True
    names = {item["name"] for item in result["checks"]}
    assert "role_assertion" in names
    assert "app_cannot_select_businesses" in names
    assert "app_cannot_select_app_users" in names
    assert "app_cannot_select_app_sessions" in names
    assert "app_cannot_insert_usage" in names
    assert "app_cannot_enable_rls_bypass_guc" in names
    assert "cannot_set_role_takyon_operator_runtime" in names
    assert conn.closed is True


def test_operator_probe_checks_business_read_and_money_write_denial(monkeypatch):
    module = _load_module()
    conn = _FakeConn(plane="operator")
    _patch_runtime(monkeypatch, module, conn=conn, role="takyon_operator_runtime")

    result = module.probe_plane("operator")

    assert result["ok"] is True
    names = {item["name"] for item in result["checks"]}
    assert "operator_can_select_businesses" in names
    assert "operator_cannot_insert_usage" in names
    assert "operator_cannot_insert_billing" in names
    assert "cannot_set_role_takyon_app_runtime" in names
    assert conn.closed is True


def test_probe_main_outputs_json_without_dsn(monkeypatch, capsys):
    module = _load_module()
    conn = _FakeConn(plane="app")
    _patch_runtime(monkeypatch, module, conn=conn, role="takyon_app_runtime")

    code = module.main(["--plane", "app"])
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert code == 0
    assert payload["ok"] is True
    assert "postgresql://redacted" not in out
