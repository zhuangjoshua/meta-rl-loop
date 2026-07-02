"""Focused tests for the Stage-3 RuntimeContext keystone (plugins/takyon/environment.py).

The exhaustive host-role truth table lives in test_takyon_host_role_characterization.py (and must
never be edited to fit the enum); this file spot-checks that the three HostRole VIEWS reproduce a
few representative rows, plus the environment-scoping/leakage contracts the caches and boot path
rely on (plan R3/R4/UC3).
"""

from __future__ import annotations

import pytest

from plugins.takyon import environment
from plugins.takyon.environment import (
    HostRole,
    ProdLeakage,
    RuntimeContext,
    assert_not_prod_leakage,
    bind_context,
    cache_scope,
    current_context,
)

_ENV_VARS_READ_BY_FROM_ENV = (
    "TAKYON_ENV",
    "TAKYON_HOST_ROLE",
    "TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS",
    "TAKYON_SAFEBOX_URL",
    "TAKYON_OPERATOR_HOST",
    "TAKYON_SUBUSER_HOST",
    "TAKYON_SAFEBOX_HOST",
    "PUBLIC_COMPANY_BASE_DOMAIN",
    "TAKYON_DASHBOARD_PUBLIC_HOST",
    "TAKYON_OPERATOR_DATABASE_URL",
    "TAKYON_APP_DATABASE_URL",
    "DATABASE_URL",
)


@pytest.fixture(autouse=True)
def _isolated_runtime_context(monkeypatch):
    """Each test starts with no bound RuntimeContext and none of the from_env inputs set,
    and never leaks a bound context into other tests in the same process."""
    for name in _ENV_VARS_READ_BY_FROM_ENV:
        monkeypatch.delenv(name, raising=False)
    token = environment._CURRENT.set(None)
    try:
        yield
    finally:
        environment._CURRENT.reset(token)


# ── HostRole: the three views (spot-checks delegating to a few truth-table rows) ────────


def test_hostrole_canonical_view_rows(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "dashboard")
    assert HostRole.canonical() == "operator"
    monkeypatch.setenv("TAKYON_HOST_ROLE", "product")
    assert HostRole.canonical() == "subuser"
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    assert HostRole.canonical() == "safebox"
    monkeypatch.setenv("TAKYON_HOST_ROLE", "nonsense-role")
    assert HostRole.canonical() == "nonsense-role"  # unknown passes through raw
    monkeypatch.delenv("TAKYON_HOST_ROLE", raising=False)
    assert HostRole.canonical() == ""


def test_hostrole_serving_view_rows(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "dashboard")
    assert HostRole.serving() == "operator"
    # divergence #1: the secret-authority host is NOT a serving role
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    assert HostRole.serving() == "combined"
    # web_server also folds "worker" to combined (no worker alias in its map)
    monkeypatch.setenv("TAKYON_HOST_ROLE", "worker")
    assert HostRole.serving() == "combined"
    monkeypatch.setenv("TAKYON_HOST_ROLE", "nonsense-role")
    assert HostRole.serving() == "combined"  # unknown -> servable default
    monkeypatch.delenv("TAKYON_HOST_ROLE", raising=False)
    assert HostRole.serving() == "combined"


def test_hostrole_bare_view_rows(monkeypatch):
    # divergence #2: bare view does NOT fold dashboard onto operator
    monkeypatch.setenv("TAKYON_HOST_ROLE", "dashboard")
    assert HostRole.bare() == "dashboard"
    monkeypatch.setenv("TAKYON_HOST_ROLE", "  OpErAtOr  ")
    assert HostRole.bare() == "operator"  # strip+lower only
    monkeypatch.setenv("TAKYON_HOST_ROLE", "all")
    assert HostRole.bare() == "all"
    monkeypatch.delenv("TAKYON_HOST_ROLE", raising=False)
    assert HostRole.bare() == ""


# ── cache_scope: the R3 partition key ────────────────────────────────────────────────────


def test_cache_scope_partitions_by_takyon_env(monkeypatch):
    monkeypatch.setenv("TAKYON_ENV", "prod")
    prod_scope = cache_scope()
    monkeypatch.setenv("TAKYON_ENV", "dev")
    dev_scope = cache_scope()
    assert prod_scope != dev_scope
    # stable within one environment
    assert cache_scope() == dev_scope


def test_cache_scope_defaults_to_prod_when_unset(monkeypatch):
    unset_scope = cache_scope()
    monkeypatch.setenv("TAKYON_ENV", "prod")
    assert cache_scope() == unset_scope  # unset and explicit prod are the SAME scope


def test_cache_scope_partitions_by_takyon_home(monkeypatch):
    monkeypatch.setenv("TAKYON_ENV", "dev")
    monkeypatch.setenv("TAKYON_HOME", "/tmp/takyon-a")
    scope_a = cache_scope()
    monkeypatch.setenv("TAKYON_HOME", "/tmp/takyon-b")
    assert cache_scope() != scope_a


def test_cache_scope_uses_bound_context(monkeypatch, tmp_path):
    monkeypatch.setenv("TAKYON_ENV", "dev")
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    bind_context(RuntimeContext.from_env())
    assert cache_scope() == f"dev|{tmp_path}"


# ── from_env / current_context ───────────────────────────────────────────────────────────


def test_from_env_rejects_bad_takyon_env(monkeypatch):
    monkeypatch.setenv("TAKYON_ENV", "staging")
    with pytest.raises(ValueError, match=r"TAKYON_ENV must be prod\|dev\|hermetic"):
        RuntimeContext.from_env()


def test_current_context_lazy_defaults_to_prod():
    ctx = current_context()
    assert ctx.name == "prod"
    assert ctx.is_prod
    # lazily bound: the same object is returned on the next call
    assert current_context() is ctx


# ── assert_not_prod_leakage: the fail-loud boot gate ─────────────────────────────────────


def test_dev_context_with_prod_safebox_url_refuses_to_boot(monkeypatch):
    monkeypatch.setenv("TAKYON_ENV", "dev")
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://67.205.158.170:8377")
    with pytest.raises(ProdLeakage, match="67.205.158.170"):
        RuntimeContext.from_env()


def test_prod_context_with_prod_literals_passes(monkeypatch):
    monkeypatch.setenv("TAKYON_ENV", "prod")
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://67.205.158.170:8377")
    ctx = RuntimeContext.from_env()  # must not raise
    assert ctx.is_prod
    assert_not_prod_leakage(ctx)  # explicit call is also a no-op for prod
    # prod defaults resolve the real hosts
    assert ctx.hosts.operator == "137.184.75.57"
    assert ctx.hosts.subuser == "134.209.123.8"


def test_dev_context_clean_environment_boots(monkeypatch, tmp_path):
    monkeypatch.setenv("TAKYON_ENV", "dev")
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    ctx = RuntimeContext.from_env()
    assert ctx.name == "dev"
    assert not ctx.is_prod
    assert ctx.providers.mode == "test"
    # non-prod defaults must not silently point at prod twins
    assert ctx.hosts.operator == ""
    assert ctx.domains.dashboard_host == ""
