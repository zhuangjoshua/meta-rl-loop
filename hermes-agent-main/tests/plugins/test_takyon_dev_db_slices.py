"""UC3 runtime slice mapping — dev control-plane DSN resolution (modularization plan Stage 3).

Two halves:

1. CHARACTERIZATION (written against untouched origin/main behavior, must never change): with
   TAKYON_ENV unset or ``prod``, each DB authority plane resolves its existing prod alias
   (``TAKYON_OPERATOR_DATABASE_URL`` etc.), missing aliases fail naming that prod alias, and the
   no-plane legacy path resolves ``DATABASE_URL``.

2. DEV MAPPING: with TAKYON_ENV=dev, each plane resolves ONLY its ``TAKYON_DEV_*`` twin
   (deposited by the Stage-3b provisioner in the dev store), FAILS CLOSED naming the dev alias
   when it is absent (the prod alias is never a fallback — that silent fallback is exactly the
   leak the environment split exists to prevent), and a resolved dev DSN containing a prod
   literal refuses via ``ProdLeakage`` before any connection is opened.

All DSNs used here carry an explicit ``connect_timeout`` so the policy layer's timeout injection
is a no-op and equality assertions stay byte-exact.
"""

from __future__ import annotations

import pytest

pytest.importorskip("psycopg")
from psycopg.conninfo import conninfo_to_dict

import plugins.takyon.runtime_app as runtime_app  # noqa: E402
from plugins.takyon import environment, safebox  # noqa: E402
from plugins.takyon.environment import ProdLeakage, RuntimeContext  # noqa: E402
from plugins.takyon.runtime_app import RuntimeNotConfigured, resolve_database_url  # noqa: E402

_PROD_ALIAS = {
    "operator": "TAKYON_OPERATOR_DATABASE_URL",
    "app": "TAKYON_APP_DATABASE_URL",
    "safebox": "TAKYON_SAFEBOX_DATABASE_URL",
    "migration": "TAKYON_MIGRATION_DATABASE_URL",
}
_DEV_ALIAS = {
    "operator": "TAKYON_DEV_OPERATOR_DATABASE_URL",
    "app": "TAKYON_DEV_RUNTIME_DATABASE_URL",
    "safebox": "TAKYON_DEV_SAFEBOX_DATABASE_URL",
    "migration": "TAKYON_DEV_MIGRATION_DATABASE_URL",
}

_ALL_DSN_ENV = (
    "DATABASE_URL",
    "POSTGRES_URL",
    "POSTGRES_PRISMA_URL",
    "POSTGRES_URL_NON_POOLING",
    "MIGRATION_DATABASE_URL",
    *_PROD_ALIAS.values(),
    *_DEV_ALIAS.values(),
)


def _dsn(label: str) -> str:
    return f"postgresql://twin.example.com/{label}?connect_timeout=7"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """No ambient DSNs, no real env file, no bound RuntimeContext, no memoised URL — each test
    assembles exactly the environment it asserts about."""
    for name in _ALL_DSN_ENV:
        monkeypatch.delenv(name, raising=False)
    for name in ("TAKYON_ENV", "TAKYON_HOST_ROLE", "TAKYON_SAFEBOX_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    # Policy passthrough: these tests pin RESOLUTION, not the VPS containment gate (which has its
    # own tests in test_takyon_runtime_app_pg.py).
    monkeypatch.setenv("TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS", "1")
    monkeypatch.setattr(safebox, "load_env", lambda: {})
    runtime_app.reset_database_url_cache()
    token = environment._CURRENT.set(None)
    try:
        yield
    finally:
        environment._CURRENT.reset(token)
        runtime_app.reset_database_url_cache()


# ── 1. characterization: prod/unset resolution is byte-identical to origin/main ─────────


@pytest.mark.parametrize("takyon_env", ["", "prod"])
@pytest.mark.parametrize("plane", sorted(_PROD_ALIAS))
def test_prod_and_unset_env_resolve_the_prod_alias(monkeypatch, takyon_env, plane):
    if takyon_env:
        monkeypatch.setenv("TAKYON_ENV", takyon_env)
    monkeypatch.setenv(_PROD_ALIAS[plane], _dsn(f"prod-{plane}"))
    # A stray dev alias must be IGNORED outside dev.
    monkeypatch.setenv(_DEV_ALIAS[plane], _dsn(f"dev-{plane}"))
    assert resolve_database_url(plane=plane) == _dsn(f"prod-{plane}")


@pytest.mark.parametrize("takyon_env", ["", "prod"])
def test_prod_and_unset_env_missing_alias_names_the_prod_alias(monkeypatch, takyon_env):
    if takyon_env:
        monkeypatch.setenv("TAKYON_ENV", takyon_env)
    with pytest.raises(RuntimeNotConfigured, match="TAKYON_OPERATOR_DATABASE_URL"):
        resolve_database_url(plane="operator")


def test_unset_env_legacy_no_plane_resolves_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", _dsn("legacy"))
    assert resolve_database_url() == _dsn("legacy")


# ── 2. dev mapping: TAKYON_ENV=dev resolves ONLY the TAKYON_DEV_* twins ──────────────────


@pytest.mark.parametrize("plane", sorted(_DEV_ALIAS))
def test_dev_plane_resolves_only_the_dev_alias(monkeypatch, plane):
    monkeypatch.setenv("TAKYON_ENV", "dev")
    monkeypatch.setenv(_DEV_ALIAS[plane], _dsn(f"dev-{plane}"))
    # The prod alias being present must NOT win (or even be consulted).
    monkeypatch.setenv(_PROD_ALIAS[plane], _dsn(f"prod-{plane}"))
    assert resolve_database_url(plane=plane) == _dsn(f"dev-{plane}")


def test_dev_alias_resolves_from_the_takyon_home_env_file(monkeypatch):
    # The provisioner deposits the dev DSNs in the dev store ($TAKYON_HOME/.env), not process env.
    monkeypatch.setenv("TAKYON_ENV", "dev")
    monkeypatch.setattr(
        safebox, "load_env", lambda: {"TAKYON_DEV_OPERATOR_DATABASE_URL": _dsn("dev-store")}
    )
    assert resolve_database_url(plane="operator") == _dsn("dev-store")


def test_dev_legacy_shared_pooler_alias_self_heals_to_transaction_pooler(monkeypatch):
    monkeypatch.setenv("TAKYON_ENV", "dev")
    legacy = (
        "postgresql://takyon_operator_runtime.devref:pw@aws-1-us-east-2.pooler.supabase.com:5432/"
        "postgres?sslmode=require"
    )
    monkeypatch.setattr(
        safebox, "load_env", lambda: {"TAKYON_DEV_OPERATOR_DATABASE_URL": legacy}
    )
    resolved = conninfo_to_dict(resolve_database_url(plane="operator"))
    assert resolved["host"] == "aws-1-us-east-2.pooler.supabase.com"
    assert resolved["port"] == "6543"
    assert resolved["dbname"] == "postgres"
    assert resolved["user"] == "takyon_operator_runtime.devref"


def test_dev_non_pooler_dsn_is_not_rewritten(monkeypatch):
    monkeypatch.setenv("TAKYON_ENV", "dev")
    direct = "postgresql://takyon_operator_runtime@db.dev.example:5432/postgres?connect_timeout=7"
    monkeypatch.setattr(
        safebox, "load_env", lambda: {"TAKYON_DEV_OPERATOR_DATABASE_URL": direct}
    )
    assert resolve_database_url(plane="operator") == direct


@pytest.mark.parametrize("plane", sorted(_DEV_ALIAS))
def test_dev_missing_dev_alias_fails_closed_naming_it(monkeypatch, plane):
    monkeypatch.setenv("TAKYON_ENV", "dev")
    # Prod alias configured — dev must NOT fall back to it.
    monkeypatch.setenv(_PROD_ALIAS[plane], _dsn(f"prod-{plane}"))
    with pytest.raises(RuntimeNotConfigured, match=_DEV_ALIAS[plane]):
        resolve_database_url(plane=plane)


def test_dev_resolved_prod_literal_dsn_refuses(monkeypatch):
    monkeypatch.setenv("TAKYON_ENV", "dev")
    monkeypatch.setenv(
        "TAKYON_DEV_OPERATOR_DATABASE_URL",
        "postgresql://takyon_operator_runtime@db.ddftvmjpfghfrdxhavvp.supabase.co:5432/postgres?connect_timeout=7",
    )
    with pytest.raises(ProdLeakage, match=r"db\.ddftvmjpfghfrdxhavvp\.supabase\.co"):
        resolve_database_url(plane="operator")


def test_dev_boot_assertion_arms_over_dev_alias_env(monkeypatch):
    monkeypatch.setenv("TAKYON_ENV", "dev")
    monkeypatch.setenv(
        "TAKYON_DEV_RUNTIME_DATABASE_URL",
        "postgresql://takyon_app_runtime@db.ddftvmjpfghfrdxhavvp.supabase.co:5432/postgres?connect_timeout=7",
    )
    with pytest.raises(ProdLeakage, match=r"db\.ddftvmjpfghfrdxhavvp\.supabase\.co"):
        RuntimeContext.from_env()


def test_prod_boot_with_dev_alias_set_is_untouched(monkeypatch):
    # The boot gate stays a NO-OP for prod: prod pointing at prod is the normal state.
    monkeypatch.setenv("TAKYON_ENV", "prod")
    monkeypatch.setenv(
        "TAKYON_DEV_RUNTIME_DATABASE_URL",
        "postgresql://takyon_app_runtime@db.ddftvmjpfghfrdxhavvp.supabase.co:5432/postgres?connect_timeout=7",
    )
    ctx = RuntimeContext.from_env()  # must not raise
    assert ctx.is_prod
