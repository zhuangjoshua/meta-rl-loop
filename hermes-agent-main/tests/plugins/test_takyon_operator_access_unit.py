from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from plugins.takyon import operator_access


class _Result:
    def __init__(self, *, one=None, all_rows=None):
        self._one = one
        self._all = list(all_rows or [])

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class _Conn:
    def __init__(self, *, one=None, all_rows=None):
        self.one = one
        self.all_rows = all_rows
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return _Result(one=self.one, all_rows=self.all_rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _context() -> operator_access.SSHOperatorContext:
    return operator_access.SSHOperatorContext(
        client_address="203.0.113.9",
        operator_host="argon-alpha-14",
    )


def test_staff_email_is_exact_domain_not_suffix_or_subdomain():
    assert (
        operator_access.normalize_staff_email(" Sai@FourManifold.com ")
        == "sai@fourmanifold.com"
    )
    for value in (
        "sai@example.com",
        "sai@fourmanifold.com.evil.test",
        "sai@dev.fourmanifold.com",
        "sai@fourmanifold.com@evil.test",
        "@fourmanifold.com",
    ):
        with pytest.raises(operator_access.OperatorAccessError):
            operator_access.normalize_staff_email(value)


def test_context_requires_root_operator_and_real_ssh_shape():
    base = {
        "TAKYON_ENV": "prod",
        "TAKYON_HOST_ROLE": "operator",
        "SSH_CONNECTION": "203.0.113.9 54321 137.184.75.57 22",
    }
    assert (
        operator_access.require_root_ssh_operator_context(
            environ=base, euid=0, hostname="argon-alpha-14"
        )
        == _context()
    )

    with pytest.raises(operator_access.OperatorAccessError, match="euid 0"):
        operator_access.require_root_ssh_operator_context(
            environ=base, euid=995, hostname="argon-alpha-14"
        )
    with pytest.raises(operator_access.OperatorAccessError, match="TAKYON_ENV=prod"):
        operator_access.require_root_ssh_operator_context(
            environ={**base, "TAKYON_ENV": "dev"}, euid=0, hostname="argon-alpha-14"
        )
    with pytest.raises(operator_access.OperatorAccessError, match="HOST_ROLE=operator"):
        operator_access.require_root_ssh_operator_context(
            environ={**base, "TAKYON_HOST_ROLE": "subuser"},
            euid=0,
            hostname="takyon-subuser",
        )
    with pytest.raises(operator_access.OperatorAccessError, match="active SSH"):
        operator_access.require_root_ssh_operator_context(
            environ={"TAKYON_ENV": "prod", "TAKYON_HOST_ROLE": "operator"},
            euid=0,
            hostname="argon-alpha-14",
        )
    with pytest.raises(operator_access.OperatorAccessError, match="malformed"):
        operator_access.require_root_ssh_operator_context(
            environ={**base, "SSH_CONNECTION": "not-an-ip 1 127.0.0.1 22"},
            euid=0,
            hostname="argon-alpha-14",
        )


def test_grant_calls_only_dedicated_role_function_with_normalized_scope():
    receipt = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        "sai@fourmanifold.com",
        "monthly",
        "paid",
        "active",
        True,
    )
    conn = _Conn(one=receipt)
    request_id = uuid.uuid4()

    result = operator_access.grant_profile_access(
        conn,
        _context(),
        business_slug="test-product",
        email="SAI@FOURMANIFOLD.COM",
        plan_key="monthly",
        request_id=request_id,
    )

    assert "operator_ssh_grant_app_access" in conn.calls[0][0]
    assert conn.calls[0][1] == (
        "test-product",
        "sai@fourmanifold.com",
        "monthly",
        request_id,
        "203.0.113.9",
        "argon-alpha-14",
    )
    assert result["action"] == "grant"
    assert result["status"] == "active"
    assert result["changed"] is True


def test_revoke_calls_only_dedicated_role_function():
    receipt = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        "josh@fourmanifold.com",
        "monthly",
        "paid",
        "revoked",
        True,
    )
    conn = _Conn(one=receipt)
    result = operator_access.revoke_profile_access(
        conn,
        _context(),
        business_slug="test-product",
        email="josh@fourmanifold.com",
    )
    assert "operator_ssh_revoke_app_access" in conn.calls[0][0]
    assert result["action"] == "revoke"
    assert result["status"] == "revoked"


def test_grant_parser_requires_explicit_plan():
    parser = operator_access._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["grant", "test-product", "sai@fourmanifold.com"])


def test_list_calls_bounded_function_not_private_table():
    row = (
        uuid.uuid4(),
        "test-product",
        uuid.uuid4(),
        "sai@fourmanifold.com",
        "monthly",
        "paid",
        "active",
        uuid.uuid4(),
        uuid.uuid4(),
        None,
        "203.0.113.9/32",
        "argon-alpha-14",
        None,
        None,
        None,
        None,
        None,
        None,
        123,
        1_000,
    )
    conn = _Conn(all_rows=[row])
    result = operator_access.list_profile_access(
        conn, business_slug="test-product", email="SAI@FOURMANIFOLD.COM"
    )
    assert "operator_ssh_list_app_access" in conn.calls[0][0]
    assert "app_operator_access_grants" not in conn.calls[0][0]
    assert conn.calls[0][1] == ("test-product", "sai@fourmanifold.com")
    assert result["grants"][0]["used_microusd"] == 123


def test_run_reads_only_root_narrow_dsn_after_ssh_check(monkeypatch):
    import psycopg

    receipt = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        "sai@fourmanifold.com",
        "monthly",
        "paid",
        "active",
        True,
    )
    conn = _Conn(one=receipt)
    seen = []
    monkeypatch.setattr(
        operator_access, "require_root_ssh_operator_context", lambda: _context()
    )
    monkeypatch.setattr(
        operator_access,
        "_read_access_database_url",
        lambda: seen.append("credential") or "postgresql://takyon_operator_access/db",
    )
    monkeypatch.setattr(
        operator_access,
        "_assert_access_database_role",
        lambda actual: seen.append(("assert", actual is conn)),
    )
    monkeypatch.setattr(
        psycopg,
        "connect",
        lambda dsn, **kwargs: seen.append(("connect", dsn, kwargs)) or conn,
    )

    result = operator_access.run([
        "grant",
        "test-product",
        "sai@fourmanifold.com",
        "--plan",
        "monthly",
    ])

    assert seen[0] == "credential"
    assert any(
        item[0] == "connect" and item[1] == "postgresql://takyon_operator_access/db"
        for item in seen
    )
    assert ("assert", True) in seen
    assert result["action"] == "grant"


def test_operator_launcher_keeps_profile_access_out_of_normal_web_cli():
    workspace = Path(__file__).resolve().parents[3]
    launcher = (workspace / "deploy" / "argon-alpha-14" / "takyon-op").read_text()
    assert '"${1:-}" == "profile-access"' in launcher
    branch = launcher.split('"${1:-}" == "profile-access"', 1)[1].split(
        "service_env_value()", 1
    )[0]
    assert "plugins.takyon.operator_access" in branch
    assert "runuser" not in branch
    assert "TAKYON_HOST_ROLE=operator" in branch
    assert "TAKYON_ENV=prod" in branch
    assert "exec env -i" in branch
    assert "TAKYON_MIGRATION_DATABASE_URL" not in branch
    assert "resolve_database_url" not in branch
    assert "/root/.config/takyon/operator-access" not in launcher
    assert "sshd -T -C" in branch
    assert '"pubkeyauthentication yes"' in branch
    assert '"passwordauthentication no"' in branch
    assert '"kbdinteractiveauthentication no"' in branch


def test_staff_account_usage_display_uses_same_calendar_month_as_money_gate():
    from plugins.takyon import core

    marker = object()
    leaf = _Conn(one=(marker,))
    period = core._entitlement_anchored_period_start(
        leaf,
        [
            {
                "status": "active",
                "tier": "paid",
                "source": "operator_ssh",
                "updated_at": "2026-07-09T00:00:00Z",
                "current_period_end": None,
            }
        ],
    )
    assert period is marker
    assert leaf.calls == [("select date_trunc('month', now())", ())]
