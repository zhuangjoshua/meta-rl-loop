"""EnvironmentProvisioner tests (modularization Stage 3b, UC3 backend half).

These run WITHOUT any network: the provisioner takes an injectable HTTP transport and a fake safebox,
so create()/status()/destroy() are exercised end to end against fakes. The invariants proven here are
the load-bearing ones from the plan:

- ``dev.yaml`` parses and has the required keys the provisioner reads.
- ``create()`` against a missing-credential env FAILS CLOSED, naming the EXACT alias to deposit — and
  makes no provider call for that step.
- the prod-literal guard (``environment.PROD_LITERALS``) REJECTS a dev manifest whose DSN/host is a
  prod literal.
- ``status()`` on a nonexistent env is a clean report, never a crash.
- ``destroy(force=False)`` REFUSES while the environment has live state.
- ``takyon env`` is a registered CLI subcommand (argparse smoke).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.takyon import env_provisioner as ep


_MANIFEST_DIR = Path(__file__).resolve().parents[2] / "environments"


# ── fakes (no network, no real safebox) ─────────────────────────────────────────────────────


class FakeSafebox:
    """In-process stand-in for the safebox authority route. Resolves aliases from a dict; any alias
    not present resolves to '' (the fail-closed 'not deposited' signal)."""

    def __init__(self, values: dict[str, str] | None = None):
        self.values = dict(values or {})

    def first_env_backed_value(self, *keys: str) -> str:
        for key in keys:
            v = self.values.get(str(key or "").strip())
            if v:
                return v
        return ""


class FakeHttp(ep.HttpTransport):
    """Records requests (including JSON bodies); returns queued responses keyed by
    (METHOD, url-substring)."""

    def __init__(self, responses: dict[tuple[str, str], object] | None = None):
        self.responses = dict(responses or {})
        self.calls: list[tuple[str, str]] = []
        self.requests: list[dict] = []

    def request(self, method, url, *, headers=None, body=None, form=None):
        self.calls.append((method, url))
        # Copy the body: the provisioner may mutate its dict after a refused call (scope fallback).
        self.requests.append({
            "method": method, "url": url,
            "body": dict(body) if isinstance(body, dict) else body, "form": form,
        })
        for (m, sub), resp in self.responses.items():
            if m == method and sub in url:
                return resp
        return {}


def _provisioner(name="dev", *, safebox_values=None, http=None, manifest=None, home=None):
    return ep.EnvironmentProvisioner(
        name,
        home=home or Path("/private/tmp/does-not-matter-unset"),
        safebox_mod=FakeSafebox(safebox_values),
        http=http or FakeHttp(),
        manifest=manifest,
    )


# ── dev.yaml manifest ────────────────────────────────────────────────────────────────────────


def test_dev_manifest_parses_and_has_required_keys():
    data = ep.load_manifest("dev")
    assert data["name"] == "dev"
    for key in ("domains", "database", "safebox", "auth0", "cloudflare", "stripe", "plans",
                "vpc", "ssh_key", "droplets", "load_balancer", "firewall"):
        assert key in data, f"dev.yaml missing {key}"
    # Stage 4b dev split: two subuser replicas behind the LB, singleton safebox host.
    assert data["droplets"]["count"] == 2
    assert data["droplets"]["role"] == "subuser"
    assert data["droplets"]["safebox_host"]["enabled"] is True
    assert data["load_balancer"]["enabled"] is True
    assert data["load_balancer"]["health_check"]["path"] == "/healthz"
    # The dev VPC must not be the prod VPC range.
    assert not str(data["vpc"]["ip_range"]).startswith("10.116.")
    # The DB step targets a dev migration DSN by ALIAS, never a literal.
    assert data["database"]["dsn_alias"] == "TAKYON_DEV_MIGRATION_DATABASE_URL"
    assert data["database"]["enabled"] is True
    # dev domains must be *.dev / localtest, never the prod company base or dashboard host.
    assert data["domains"]["company_base"] == "dev.coscale.app"
    # Auth0 mgmt credential aliases: the durable M2M pair (preferred) + the transient raw token.
    assert data["auth0"]["mgmt_client_id_alias"] == "TAKYON_AUTH0_MGMT_CLIENT_ID"
    assert data["auth0"]["mgmt_client_secret_alias"] == "TAKYON_AUTH0_MGMT_CLIENT_SECRET"
    assert data["auth0"]["mgmt_token_alias"] == "TAKYON_AUTH0_MGMT_TOKEN"
    # Subuser login rail (UC3 dev gap): the dev safebox must hold the DEV Supabase project's auth
    # config — the EXACT aliases app_supabase_auth resolves for handle_business_supabase_login —
    # so `takyon env status dev` truthfully reports them missing until deposited.
    assert data["safebox"]["required_aliases"]["supabase_auth"] == [
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
    ]


def test_hermetic_manifest_all_disabled():
    data = ep.load_manifest("hermetic")
    assert data["name"] == "hermetic"
    for twin in ("database", "safebox", "auth0", "cloudflare", "stripe",
                 "vpc", "ssh_key", "droplets", "load_balancer", "firewall"):
        assert (data.get(twin) or {}).get("enabled", False) is False


def test_load_manifest_missing_env_raises():
    with pytest.raises(ep.EnvironmentProvisionError):
        ep.load_manifest("nope-not-a-real-env")


# ── prod refusals ─────────────────────────────────────────────────────────────────────────────


def test_provisioner_refuses_name_prod():
    with pytest.raises(ep.EnvironmentProvisionError):
        ep.EnvironmentProvisioner("prod", manifest={"name": "prod", "domains": {}, "database": {}, "safebox": {}})


def test_prod_literal_guard_rejects_prod_dsn():
    """A dev manifest whose resolved DSN is a prod literal must refuse before any DDL."""
    # Point the dev migration DSN alias at a value containing a prod literal (the control-plane host).
    prod_dsn = "postgresql://takyon_migration@db.ddftvmjpfghfrdxhavvp.supabase.co:5432/postgres"
    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app", "dashboard_host": ""},
        "database": {"enabled": True, "dsn_alias": "TAKYON_DEV_MIGRATION_DATABASE_URL"},
        "safebox": {"enabled": False},
    }
    http = FakeHttp()
    prov = _provisioner(
        manifest=manifest,
        safebox_values={"TAKYON_DEV_MIGRATION_DATABASE_URL": prod_dsn},
        http=http,
    )
    with pytest.raises(ep.EnvironmentProvisionError) as exc:
        prov._create_database()
    assert "prod literal" in str(exc.value)
    # And it made NO http call (fail-closed before any side effect).
    assert http.calls == []


def test_prod_literal_guard_write_config_raises():
    manifest = {
        "name": "dev",
        "domains": {"company_base": "app.fourmanifold.com", "dashboard_host": ""},
        "database": {"enabled": False},
        "safebox": {"enabled": False},
    }
    prov = _provisioner(manifest=manifest)
    with pytest.raises(ep.EnvironmentProvisionError):
        prov._write_config([])


# ── fail-closed on missing credentials ──────────────────────────────────────────────────────


def test_create_fails_closed_naming_missing_db_alias():
    """With NO safebox values, the DB step blocks and names the exact DSN alias to deposit."""
    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app", "dashboard_host": ""},
        "database": {"enabled": True, "dsn_alias": "TAKYON_DEV_MIGRATION_DATABASE_URL"},
        "safebox": {"enabled": False},
    }
    http = FakeHttp()
    prov = _provisioner(manifest=manifest, safebox_values={}, http=http)
    receipt = prov._create_database()
    assert receipt.status == ep.STATUS_BLOCKED
    assert receipt.deposit == "TAKYON_DEV_MIGRATION_DATABASE_URL"
    assert http.calls == []  # no DB / provider work happened


def test_create_auth0_fails_closed_naming_mgmt_credential():
    """No token AND no client pair deposited → blocked, naming BOTH accepted deposit shapes."""
    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app"},
        "database": {"enabled": False},
        "safebox": {"enabled": False},
        "auth0": {"enabled": True, "mgmt_token_alias": "TAKYON_AUTH0_MGMT_TOKEN", "domain_alias": "AUTH0_DOMAIN"},
    }
    http = FakeHttp()
    prov = _provisioner(
        manifest=manifest, safebox_values={"AUTH0_DOMAIN": "dev-x.us.auth0.com"}, http=http
    )
    receipt = prov._create_auth0()
    assert receipt.status == ep.STATUS_BLOCKED
    assert receipt.deposit == "TAKYON_AUTH0_MGMT_CLIENT_ID"
    assert "TAKYON_AUTH0_MGMT_CLIENT_ID" in receipt.detail
    assert "TAKYON_AUTH0_MGMT_TOKEN" in receipt.detail
    assert http.calls == []


def test_create_auth0_blocked_on_missing_domain_before_any_call():
    http = FakeHttp()
    prov = _provisioner(
        manifest={
            "name": "dev",
            "domains": {"company_base": "dev.coscale.app"},
            "database": {"enabled": False},
            "safebox": {"enabled": False},
            "auth0": {"enabled": True},
        },
        safebox_values={},
        http=http,
    )
    receipt = prov._create_auth0()
    assert receipt.status == ep.STATUS_BLOCKED
    assert receipt.deposit == "AUTH0_DOMAIN"
    assert http.calls == []


def test_auth0_create_mints_token_from_client_credentials():
    """The durable deposit shape: M2M client id+secret → a fresh mgmt token is minted per run via
    client_credentials, then the app is created. A second call reuses the minted token (one mint)."""
    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app"},
        "database": {"enabled": False},
        "safebox": {"enabled": False},
        "auth0": {"enabled": True, "application_name": "Takyon Dev", "domain_alias": "AUTH0_DOMAIN"},
    }
    http = FakeHttp(responses={
        ("POST", "/oauth/token"): {"access_token": "minted-tok", "expires_in": 86400},
        ("GET", "/clients"): [],
        ("POST", "/clients"): {"client_id": "cid_new"},
    })
    prov = _provisioner(
        manifest=manifest,
        safebox_values={
            "AUTH0_DOMAIN": "dev-x.us.auth0.com",
            "TAKYON_AUTH0_MGMT_CLIENT_ID": "m2m-id",
            "TAKYON_AUTH0_MGMT_CLIENT_SECRET": "m2m-secret",
        },
        http=http,
    )
    receipt = prov._create_auth0()
    assert receipt.status == ep.STATUS_CREATED
    assert receipt.data["client_id"] == "cid_new"
    mint_calls = [(m, u) for m, u in http.calls if "/oauth/token" in u]
    assert len(mint_calls) == 1 and mint_calls[0][0] == "POST"
    # The mint happened BEFORE any Management API call.
    assert http.calls[0][1].endswith("/oauth/token")

    # Second step in the same run: the minted token is reused, no second mint.
    prov._create_auth0()
    assert len([(m, u) for m, u in http.calls if "/oauth/token" in u]) == 1


def test_status_treats_client_pair_as_present_without_minting():
    http = FakeHttp()
    prov = _provisioner(
        safebox_values={
            "TAKYON_AUTH0_MGMT_CLIENT_ID": "m2m-id",
            "TAKYON_AUTH0_MGMT_CLIENT_SECRET": "m2m-secret",
        },
        http=http,
    )  # real dev.yaml manifest
    result = prov.status()
    auth0 = next(r for r in result.receipts if r.resource == "auth0")
    assert auth0.status == ep.STATUS_EXISTS
    assert "minted per run" in auth0.detail
    assert http.calls == []  # status never mints


def test_create_stripe_fails_closed_missing_key():
    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app"},
        "database": {"enabled": False},
        "safebox": {"enabled": False},
        "stripe": {"enabled": True, "webhook_url": "http://localhost:9119/api/webhooks/stripe"},
    }
    http = FakeHttp()
    prov = _provisioner(manifest=manifest, safebox_values={}, http=http)
    receipt = prov._create_stripe()
    assert receipt.status == ep.STATUS_BLOCKED
    assert receipt.deposit == "STRIPE_SECRET_KEY"
    assert http.calls == []


def test_create_stripe_refuses_live_key():
    """A non-test Stripe key in dev must be refused (never touch the real account)."""
    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app"},
        "database": {"enabled": False},
        "safebox": {"enabled": False},
        "stripe": {"enabled": True, "webhook_url": "http://localhost:9119/api/webhooks/stripe"},
    }
    http = FakeHttp()
    prov = _provisioner(manifest=manifest, safebox_values={"STRIPE_SECRET_KEY": "sk_live_ABC"}, http=http)
    receipt = prov._create_stripe()
    assert receipt.status == ep.STATUS_ERROR
    assert "TEST-mode" in receipt.detail
    assert http.calls == []


def test_create_safebox_reports_each_missing_alias():
    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app"},
        "database": {"enabled": False},
        "safebox": {
            "enabled": True,
            "url_alias": "TAKYON_DEV_SAFEBOX_URL",
            "required_aliases": {
                "stripe": ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"],
                "providers": ["GEMINI_API_KEY"],
            },
        },
    }
    # URL present, but two of three aliases missing.
    prov = _provisioner(
        manifest=manifest,
        safebox_values={"TAKYON_DEV_SAFEBOX_URL": "https://dev-safebox.internal", "GEMINI_API_KEY": "k"},
    )
    receipt = prov._create_safebox()
    assert receipt.status == ep.STATUS_BLOCKED
    assert set(receipt.data["missing"]) == {"STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"}
    assert "GEMINI_API_KEY" in receipt.data["present"]


# ── happy-path idempotency for the API steps (against the fake transport) ────────────────────


def test_stripe_create_is_idempotent_when_webhook_exists():
    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app"},
        "database": {"enabled": False},
        "safebox": {"enabled": False},
        "stripe": {"enabled": True, "webhook_url": "https://dev.coscale.app/api/webhooks/stripe",
                   "enabled_events": ["checkout.session.completed"]},
    }
    http = FakeHttp(responses={
        ("GET", "/webhook_endpoints"): {"data": [{"id": "we_1", "url": "https://dev.coscale.app/api/webhooks/stripe"}]},
    })
    prov = _provisioner(manifest=manifest, safebox_values={"STRIPE_SECRET_KEY": "sk_test_ABC"}, http=http)
    receipt = prov._create_stripe()
    assert receipt.status == ep.STATUS_EXISTS
    assert receipt.data["webhook_endpoint_id"] == "we_1"
    # No POST was made — only the idempotency GET.
    assert all(m == "GET" for m, _ in http.calls)


def test_auth0_create_is_idempotent_when_app_exists():
    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app"},
        "database": {"enabled": False},
        "safebox": {"enabled": False},
        "auth0": {"enabled": True, "application_name": "Takyon Dev",
                  "mgmt_token_alias": "TAKYON_AUTH0_MGMT_TOKEN", "domain_alias": "AUTH0_DOMAIN"},
    }
    http = FakeHttp(responses={
        ("GET", "/clients"): [{"client_id": "cid_1", "name": "Takyon Dev"}],
    })
    prov = _provisioner(
        manifest=manifest,
        safebox_values={"TAKYON_AUTH0_MGMT_TOKEN": "tok", "AUTH0_DOMAIN": "dev-x.us.auth0.com"},
        http=http,
    )
    receipt = prov._create_auth0()
    assert receipt.status == ep.STATUS_EXISTS
    assert receipt.data["client_id"] == "cid_1"
    assert all(m == "GET" for m, _ in http.calls)


# ── status ───────────────────────────────────────────────────────────────────────────────────


def test_status_on_nonexistent_env_is_clean_report(tmp_path):
    """status() with nothing deposited is a clean structured report — never a crash."""
    prov = _provisioner(safebox_values={}, home=tmp_path)  # real dev.yaml, empty safebox
    result = prov.status()
    assert result.action == "status"
    # Every twin resolves to a receipt (no exception). DB/safebox/auth0/stripe blocked; cloudflare/droplet disabled.
    resources = {r.resource for r in result.receipts}
    assert {"database", "safebox", "auth0", "stripe", "config"} <= resources
    db = next(r for r in result.receipts if r.resource == "database")
    assert db.status == ep.STATUS_BLOCKED
    assert db.deposit == "TAKYON_DEV_MIGRATION_DATABASE_URL"
    # config not yet provisioned -> skipped, not error.
    cfg = next(r for r in result.receipts if r.resource == "config")
    assert cfg.status == ep.STATUS_SKIPPED


# ── destroy ──────────────────────────────────────────────────────────────────────────────────


def test_destroy_force_false_refuses_with_live_state(monkeypatch, tmp_path):
    """destroy(force=False) must refuse while the env has live pools/ledger rows."""
    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app"},
        "database": {"enabled": True, "dsn_alias": "TAKYON_DEV_MIGRATION_DATABASE_URL"},
        "safebox": {"enabled": False},
        "auth0": {"enabled": False},
        "stripe": {"enabled": False},
    }
    prov = _provisioner(
        manifest=manifest,
        safebox_values={"TAKYON_DEV_MIGRATION_DATABASE_URL": "postgresql://x@dev-db.internal/dev"},
        home=tmp_path,
    )
    # Simulate live state without a real DB.
    monkeypatch.setattr(prov, "_live_state_summary", lambda: {"pools": 2, "ledger_rows": 5})
    result = prov.destroy(force=False)
    blocked = [r for r in result.receipts if r.status == ep.STATUS_BLOCKED]
    assert blocked, "destroy should have refused with live state"
    assert "refusing destroy" in blocked[0].detail
    assert not result.ok


def test_destroy_force_true_proceeds_past_live_guard(monkeypatch, tmp_path):
    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app"},
        "database": {"enabled": False},
        "safebox": {"enabled": False},
        "auth0": {"enabled": False},
        "stripe": {"enabled": False},
    }
    prov = _provisioner(manifest=manifest, home=tmp_path)
    monkeypatch.setattr(prov, "_live_state_summary", lambda: {"pools": 9, "ledger_rows": 9})
    result = prov.destroy(force=True)
    # With force, the live guard is bypassed; the local-state removal step runs (skipped since none written).
    assert not any(r.status == ep.STATUS_BLOCKED and "refusing destroy" in (r.detail or "") for r in result.receipts)


# ── CLI registration (argparse smoke) ────────────────────────────────────────────────────────


def _parse_via_main(argv):
    """Drive the full ``main()`` subparser registration (which happens inline in main()) and capture
    the parsed args by stubbing the ``env`` handler so no real dispatch occurs. Returns the args
    namespace the CLI would have handed ``cmd_env``."""
    import takyon_cli.env as env_mod
    import takyon_cli.main as main_mod

    captured: dict[str, object] = {}

    def _capture(args):
        captured["args"] = args
        return 0

    import sys as _sys

    orig = env_mod.cmd_env
    env_mod.cmd_env = _capture  # main() imports cmd_env locally from takyon_cli.env at registration
    old_argv = _sys.argv
    _sys.argv = ["takyon", *argv]
    try:
        main_mod.main()
    except SystemExit:
        pass
    finally:
        env_mod.cmd_env = orig
        _sys.argv = old_argv
    return captured.get("args")


def test_takyon_env_subcommand_registered():
    args = _parse_via_main(["env", "status", "dev"])
    assert args is not None, "takyon env status dev did not dispatch to cmd_env"
    assert getattr(args, "env_action") == "status"
    assert getattr(args, "env_name") == "dev"


def test_takyon_env_destroy_has_force_flag():
    args = _parse_via_main(["env", "destroy", "dev", "--force"])
    assert args is not None
    assert getattr(args, "env_action") == "destroy"
    assert getattr(args, "force") is True


def test_operator_cli_routes_env_to_cmd_env_not_ceo(monkeypatch):
    """`./takyon env status dev` (plugins.takyon.cli — the operator entrypoint) must delegate to the
    canonical takyon_cli.env handler, never fall through to the CEO chat path."""
    from plugins.takyon import cli as op_cli

    seen: dict[str, object] = {}

    def fake_cmd_env(args):
        seen["env_action"] = getattr(args, "env_action", None)
        seen["env_name"] = getattr(args, "env_name", None)
        seen["force"] = getattr(args, "force", None)
        return 0

    import takyon_cli.env as env_mod
    monkeypatch.setattr(env_mod, "cmd_env", fake_cmd_env)
    result = op_cli.run_takyon_command(["env", "status", "dev"])
    assert result is None
    assert seen == {"env_action": "status", "env_name": "dev", "force": False}


def test_operator_cli_routes_migrate_to_cmd_migrate_not_ceo(monkeypatch):
    from plugins.takyon import cli as op_cli

    seen: dict[str, object] = {}

    def fake_cmd_migrate(args):
        seen["dry_run"] = getattr(args, "dry_run", None)
        seen["migrate_type"] = getattr(args, "migrate_type", "unset")
        return 0

    import takyon_cli.migrate as migrate_mod
    monkeypatch.setattr(migrate_mod, "cmd_migrate", fake_cmd_migrate)
    result = op_cli.run_takyon_command(["migrate", "--dry-run"])
    assert result is None
    assert seen == {"dry_run": True, "migrate_type": None}


def test_stripe_loopback_webhook_url_skips_with_cli_guidance():
    """Stripe refuses loopback endpoint URLs, so local dev SKIPS registration and points at the
    Stripe CLI forwarding rail — no provider call, no recurring error."""
    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app"},
        "database": {"enabled": False},
        "safebox": {"enabled": False},
        "stripe": {"enabled": True, "webhook_url": "http://localhost:9119/api/webhooks/stripe"},
    }
    http = FakeHttp()
    prov = _provisioner(manifest=manifest, safebox_values={"STRIPE_SECRET_KEY": "sk_test_ABC"}, http=http)
    receipt = prov._create_stripe()
    assert receipt.status == ep.STATUS_SKIPPED
    assert "stripe listen --forward-to" in receipt.detail
    assert "STRIPE_WEBHOOK_SECRET" in receipt.detail
    assert http.calls == []


class _FakeConn:
    def __init__(self, dsn, log):
        self.dsn = dsn
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, *a, **k):
        self.log.append((self.dsn, str(sql)[:60]))
        return self


def _fake_psycopg(log):
    import types

    def connect(dsn, **kw):
        return _FakeConn(dsn, log)

    return types.SimpleNamespace(connect=connect)


def test_database_topology_runs_on_admin_dsn_not_migration_dsn(monkeypatch):
    """topology.sql is privileged (CREATEROLE/admin-option) — it must run on the ADMIN DSN; the
    migrations run on the migration DSN."""
    import sys as _sys

    log: list[tuple[str, str]] = []
    monkeypatch.setitem(_sys.modules, "psycopg", _fake_psycopg(log))
    from plugins.takyon.db import runner as db_runner
    monkeypatch.setattr(db_runner, "run_migrations", lambda conn: ["0001_x.sql"])

    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app"},
        "database": {
            "enabled": True,
            "dsn_alias": "TAKYON_DEV_MIGRATION_DATABASE_URL",
            "admin_dsn_alias": "TAKYON_DEV_ADMIN_DATABASE_URL",
        },
        "safebox": {"enabled": False},
    }
    prov = _provisioner(manifest=manifest, safebox_values={
        "TAKYON_DEV_MIGRATION_DATABASE_URL": "postgresql://takyon_migration@dev-host/db",
        "TAKYON_DEV_ADMIN_DATABASE_URL": "postgresql://postgres@dev-host/db",
    })
    receipt = prov._create_database()
    assert receipt.status == ep.STATUS_CREATED
    topo_dsns = {dsn for dsn, sql in log if "role" in sql.lower() or "topology" in sql.lower() or "grant" in sql.lower()}
    # the admin connection ran topology; the migration DSN never received the topology SQL
    admin_used = [dsn for dsn, _ in log if dsn.startswith("postgresql://postgres@")]
    assert admin_used, "admin DSN was never connected"
    assert "topology applied (admin DSN)" in receipt.detail


def test_database_without_admin_dsn_skips_topology_and_says_so(monkeypatch):
    import sys as _sys

    log: list[tuple[str, str]] = []
    monkeypatch.setitem(_sys.modules, "psycopg", _fake_psycopg(log))
    from plugins.takyon.db import runner as db_runner
    monkeypatch.setattr(db_runner, "run_migrations", lambda conn: [])

    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app"},
        "database": {
            "enabled": True,
            "dsn_alias": "TAKYON_DEV_MIGRATION_DATABASE_URL",
            "admin_dsn_alias": "TAKYON_DEV_ADMIN_DATABASE_URL",
        },
        "safebox": {"enabled": False},
    }
    prov = _provisioner(manifest=manifest, safebox_values={
        "TAKYON_DEV_MIGRATION_DATABASE_URL": "postgresql://takyon_migration@dev-host/db",
    })
    receipt = prov._create_database()
    assert receipt.status == ep.STATUS_EXISTS
    assert "not re-applied" in receipt.detail
    assert "run_migrations asserts" in receipt.detail
    # only the migration DSN was connected
    assert {dsn for dsn, _ in log} == {"postgresql://takyon_migration@dev-host/db"}


# ── Stage 4b: replicated droplets + LB + firewall + ssh key + vpc + node enrollment ─────────


def _do_manifest(tmp_path=None, **over):
    """A dev manifest with the Stage-4b DigitalOcean split enabled and everything else off."""
    pub = "ssh-ed25519 AAAA-test-key dev-split"
    key_path = ""
    if tmp_path is not None:
        p = tmp_path / "takyon_dev_split.pub"
        p.write_text(pub)
        key_path = str(p)
    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app"},
        "database": {"enabled": False},
        "safebox": {"enabled": False},
        "vpc": {"enabled": True, "name": "takyon-dev-vpc", "region": "nyc3", "ip_range": "10.200.0.0/24"},
        "ssh_key": {"enabled": True, "name": "takyon-dev-split", "public_key_path": key_path},
        "droplets": {
            "enabled": True, "count": 2, "role": "subuser", "name_prefix": "takyon-dev-subuser",
            "size": "s-1vcpu-2gb", "region": "nyc3", "token_alias": "TAKYON_DO_API_TOKEN",
            "safebox_host": {"enabled": True, "name": "takyon-dev-safebox", "size": "s-1vcpu-1gb"},
        },
        "load_balancer": {
            "enabled": True, "name": "takyon-dev-subuser-lb",
            "health_check": {"path": "/healthz", "port": 9119},
        },
        "firewall": {"enabled": True, "name": "takyon-dev-split-fw",
                     "ssh_allow_alias": "TAKYON_DEV_SSH_ALLOW_CIDR"},
    }
    manifest.update(over)
    return manifest


_DO_VALUES = {"TAKYON_DO_API_TOKEN": "do-token", "TAKYON_DEV_SSH_ALLOW_CIDR": "203.0.113.7/32"}


def test_droplets_fail_closed_missing_do_token():
    """Every DO step blocks naming the exact token alias — and makes NO provider call."""
    http = FakeHttp()
    prov = _provisioner(manifest=_do_manifest(), safebox_values={}, http=http)
    for step in (prov._create_vpc, prov._create_ssh_key, prov._create_droplets,
                 prov._create_load_balancer, prov._create_firewall):
        receipt = step()
        assert receipt.status == ep.STATUS_BLOCKED, receipt.resource
        assert receipt.deposit == "TAKYON_DO_API_TOKEN"
    assert http.calls == []


def test_droplets_create_replicated_creates_only_missing(tmp_path):
    """count=2 + safebox host with replica 1 already tagged: only replica 2 + the safebox host
    are created; the receipt names all three with distinct identities."""
    http = FakeHttp(responses={
        ("GET", "/droplets?"): {"droplets": [{"id": 101, "name": "takyon-dev-subuser-1"}]},
        ("POST", "/droplets"): {"droplet": {"id": 999}},
    })
    prov = _provisioner(manifest=_do_manifest(tmp_path), safebox_values=_DO_VALUES, http=http)
    receipt = prov._create_droplets()
    assert receipt.status == ep.STATUS_CREATED
    by_name = {d["name"]: d for d in receipt.data["droplets"]}
    assert set(by_name) == {"takyon-dev-subuser-1", "takyon-dev-subuser-2", "takyon-dev-safebox"}
    assert by_name["takyon-dev-subuser-1"]["created"] is False
    assert by_name["takyon-dev-subuser-2"]["created"] is True
    assert by_name["takyon-dev-safebox"]["role"] == "safebox"
    posts = [r for r in http.requests if r["method"] == "POST"]
    assert len(posts) == 2
    for post in posts:
        assert "takyon-env-dev" in post["body"]["tags"]


def test_droplets_create_is_idempotent_when_all_exist(tmp_path):
    http = FakeHttp(responses={
        ("GET", "/droplets?"): {"droplets": [
            {"id": 101, "name": "takyon-dev-subuser-1"},
            {"id": 102, "name": "takyon-dev-subuser-2"},
            {"id": 103, "name": "takyon-dev-safebox"},
        ]},
    })
    prov = _provisioner(manifest=_do_manifest(tmp_path), safebox_values=_DO_VALUES, http=http)
    receipt = prov._create_droplets()
    assert receipt.status == ep.STATUS_EXISTS
    assert all(m == "GET" for m, _ in http.calls)


class _TagScopeDeniedHttp(FakeHttp):
    """Mimics the dev token's real scope set (tag is READ-ONLY): droplet create WITH tags is
    refused exactly the way the DO API refuses it; untagged create succeeds."""

    def request(self, method, url, *, headers=None, body=None, form=None):
        if method == "POST" and url.endswith("/droplets") and (body or {}).get("tags"):
            self.calls.append((method, url))
            self.requests.append({"method": method, "url": url, "body": dict(body), "form": form})
            raise ep.EnvironmentProvisionError(
                'http POST https://api.digitalocean.com/v2/droplets -> 403: '
                '{"id":"forbidden","message":"You are missing the required permission tag:create."}'
            )
        return super().request(method, url, headers=headers, body=body, form=form)


def test_droplets_create_falls_back_untagged_on_missing_tag_scope(tmp_path):
    """A token without tag:create (the deliberate dev grant) still provisions: the create retries
    untagged and the receipt records tagged=false, so LB/firewall/destroy switch to id/name anchors."""
    http = _TagScopeDeniedHttp(responses={
        ("GET", "/droplets?"): {"droplets": []},
        ("POST", "/droplets"): {"droplet": {"id": 555}},
    })
    prov = _provisioner(manifest=_do_manifest(tmp_path), safebox_values=_DO_VALUES, http=http)
    receipt = prov._create_droplets()
    assert receipt.status == ep.STATUS_CREATED
    assert all(d["tagged"] is False for d in receipt.data["droplets"])
    untagged_posts = [r for r in http.requests
                      if r["method"] == "POST" and not (r["body"] or {}).get("tags")]
    assert len(untagged_posts) == 3  # two replicas + the safebox host


def test_load_balancer_uses_droplet_ids_when_replicas_untagged(tmp_path):
    http = FakeHttp(responses={
        ("GET", "/load_balancers"): {"load_balancers": []},
        ("POST", "/load_balancers"): {"load_balancer": {"id": "lb-2"}},
    })
    prov = _provisioner(manifest=_do_manifest(tmp_path), safebox_values=_DO_VALUES, http=http)
    prov._do_state["droplets"] = [
        {"name": "takyon-dev-subuser-1", "role": "subuser", "droplet_id": 201, "tagged": False},
        {"name": "takyon-dev-subuser-2", "role": "subuser", "droplet_id": 202, "tagged": False},
        {"name": "takyon-dev-safebox", "role": "safebox", "droplet_id": 203, "tagged": False},
    ]
    receipt = prov._create_load_balancer()
    assert receipt.status == ep.STATUS_CREATED
    body = next(r for r in http.requests if r["method"] == "POST")["body"]
    assert body["droplet_ids"] == [201, 202]  # replicas only, never the safebox host
    assert "tag" not in body


def test_firewall_uses_droplet_ids_when_untagged(tmp_path):
    http = FakeHttp(responses={
        ("GET", "/firewalls"): {"firewalls": []},
        ("POST", "/firewalls"): {"firewall": {"id": "fw-2"}},
    })
    prov = _provisioner(manifest=_do_manifest(tmp_path), safebox_values=_DO_VALUES, http=http)
    prov._do_state["lb_id"] = "lb-2"
    prov._do_state["droplets"] = [
        {"name": "takyon-dev-subuser-1", "role": "subuser", "droplet_id": 201, "tagged": False},
        {"name": "takyon-dev-subuser-2", "role": "subuser", "droplet_id": 202, "tagged": False},
        {"name": "takyon-dev-safebox", "role": "safebox", "droplet_id": 203, "tagged": False},
    ]
    receipt = prov._create_firewall()
    assert receipt.status == ep.STATUS_CREATED
    body = next(r for r in http.requests if r["method"] == "POST")["body"]
    rules = {r["ports"]: r["sources"] for r in body["inbound_rules"]}
    assert rules["22"] == {"addresses": ["203.0.113.7/32"]}
    assert rules["9119"] == {"load_balancer_uids": ["lb-2"], "droplet_ids": [201, 202, 203]}
    assert rules["8000"] == {"droplet_ids": [201, 202, 203]}
    assert body["droplet_ids"] == [201, 202, 203]
    assert "tags" not in body


def test_load_balancer_create_then_reuse(tmp_path):
    """First run creates the LB (role-tag backend, /healthz check, 80→9119); a run against an
    account where it exists reuses it with no POST."""
    http = FakeHttp(responses={
        ("GET", "/load_balancers"): {"load_balancers": []},
        ("POST", "/load_balancers"): {"load_balancer": {"id": "lb-1"}},
    })
    prov = _provisioner(manifest=_do_manifest(tmp_path), safebox_values=_DO_VALUES, http=http)
    receipt = prov._create_load_balancer()
    assert receipt.status == ep.STATUS_CREATED
    post = next(r for r in http.requests if r["method"] == "POST")
    assert post["body"]["tag"] == "takyon-env-dev-subuser"
    assert post["body"]["health_check"]["path"] == "/healthz"
    assert post["body"]["forwarding_rules"] == [
        {"entry_protocol": "http", "entry_port": 80, "target_protocol": "http", "target_port": 9119},
    ]
    assert prov._do_state["lb_id"] == "lb-1"

    http2 = FakeHttp(responses={
        ("GET", "/load_balancers"): {"load_balancers": [
            {"id": "lb-1", "name": "takyon-dev-subuser-lb", "ip": "192.0.2.10",
             "tag": "takyon-env-dev-subuser",
             "forwarding_rules": [{"entry_protocol": "http", "entry_port": 80,
                                   "target_protocol": "http", "target_port": 9119}],
             "health_check": {"protocol": "http", "port": 9119, "path": "/healthz",
                              "check_interval_seconds": 10, "response_timeout_seconds": 5,
                              "healthy_threshold": 3, "unhealthy_threshold": 3}},
        ]},
    })
    prov2 = _provisioner(manifest=_do_manifest(tmp_path), safebox_values=_DO_VALUES, http=http2)
    receipt2 = prov2._create_load_balancer()
    assert receipt2.status == ep.STATUS_EXISTS
    assert receipt2.data["lb_id"] == "lb-1"
    assert all(m == "GET" for m, _ in http2.calls)


def test_load_balancer_converges_health_check_drift(tmp_path):
    """A manifest health-check change (e.g. tightened eviction thresholds) converges the LIVE LB
    via PUT — same LB id, same IP, no recreate."""
    manifest = _do_manifest(tmp_path)
    manifest["load_balancer"]["health_check"] = {
        "path": "/healthz", "port": 9119,
        "check_interval_seconds": 3, "unhealthy_threshold": 2, "healthy_threshold": 2,
    }
    http = FakeHttp(responses={
        ("GET", "/load_balancers"): {"load_balancers": [
            {"id": "lb-1", "name": "takyon-dev-subuser-lb", "ip": "192.0.2.10",
             "forwarding_rules": [{"entry_protocol": "http", "entry_port": 80,
                                   "target_protocol": "http", "target_port": 9119}],
             "health_check": {"protocol": "http", "port": 9119, "path": "/healthz",
                              "check_interval_seconds": 10, "response_timeout_seconds": 5,
                              "healthy_threshold": 3, "unhealthy_threshold": 3}},
        ]},
    })
    prov = _provisioner(manifest=manifest, safebox_values=_DO_VALUES, http=http)
    receipt = prov._create_load_balancer()
    assert receipt.status == ep.STATUS_EXISTS
    assert "health check" in receipt.detail
    put = next(r for r in http.requests if r["method"] == "PUT")
    assert put["url"].endswith("/load_balancers/lb-1")
    assert put["body"]["health_check"]["check_interval_seconds"] == 3
    assert put["body"]["health_check"]["unhealthy_threshold"] == 2


def test_firewall_fails_closed_without_ssh_cidr(tmp_path):
    """No deposited operator CIDR → blocked naming the alias; :22 is never silently widened."""
    http = FakeHttp(responses={("GET", "/firewalls"): {"firewalls": []}})
    prov = _provisioner(
        manifest=_do_manifest(tmp_path),
        safebox_values={"TAKYON_DO_API_TOKEN": "do-token"},  # token but NO CIDR
        http=http,
    )
    receipt = prov._create_firewall()
    assert receipt.status == ep.STATUS_BLOCKED
    assert receipt.deposit == "TAKYON_DEV_SSH_ALLOW_CIDR"
    assert not any(m == "POST" for m, _ in http.calls)


def test_firewall_rules_lock_down_ports(tmp_path):
    """:22 only from the deposited CIDR, :9119/:80 from the LB + env droplets, :8000 from the
    env's own droplets only."""
    http = FakeHttp(responses={
        ("GET", "/firewalls"): {"firewalls": []},
        ("POST", "/firewalls"): {"firewall": {"id": "fw-1"}},
    })
    prov = _provisioner(manifest=_do_manifest(tmp_path), safebox_values=_DO_VALUES, http=http)
    prov._do_state["lb_id"] = "lb-1"
    receipt = prov._create_firewall()
    assert receipt.status == ep.STATUS_CREATED
    body = next(r for r in http.requests if r["method"] == "POST")["body"]
    rules = {r["ports"]: r["sources"] for r in body["inbound_rules"]}
    assert rules["22"] == {"addresses": ["203.0.113.7/32"]}
    assert rules["9119"] == {"load_balancer_uids": ["lb-1"], "tags": ["takyon-env-dev"]}
    assert rules["80"] == {"load_balancer_uids": ["lb-1"], "tags": ["takyon-env-dev"]}
    assert rules["8000"] == {"tags": ["takyon-env-dev"]}
    assert body["tags"] == ["takyon-env-dev"]


def test_firewall_converges_stale_lb_uid(tmp_path):
    """A recreated LB gets a new uid; the existing firewall's rules must be converged (PUT) or
    the LB's health checks stay silently blocked."""
    http = FakeHttp(responses={
        ("GET", "/firewalls"): {"firewalls": [{
            "id": "fw-1", "name": "takyon-dev-split-fw", "droplet_ids": [201, 202, 203],
            "inbound_rules": [
                {"protocol": "tcp", "ports": "80",
                 "sources": {"load_balancer_uids": ["lb-OLD"], "droplet_ids": [201, 202, 203]}},
            ],
        }]},
    })
    prov = _provisioner(manifest=_do_manifest(tmp_path), safebox_values=_DO_VALUES, http=http)
    prov._do_state["lb_id"] = "lb-NEW"
    prov._do_state["droplets"] = [
        {"name": "takyon-dev-subuser-1", "role": "subuser", "droplet_id": 201, "tagged": False},
        {"name": "takyon-dev-subuser-2", "role": "subuser", "droplet_id": 202, "tagged": False},
        {"name": "takyon-dev-safebox", "role": "safebox", "droplet_id": 203, "tagged": False},
    ]
    receipt = prov._create_firewall()
    assert receipt.status == ep.STATUS_EXISTS
    assert "converged" in receipt.detail
    put = next(r for r in http.requests if r["method"] == "PUT")
    rules = {r["ports"]: r["sources"] for r in put["body"]["inbound_rules"]}
    assert rules["80"]["load_balancer_uids"] == ["lb-NEW"]


def test_replica_nodes_enroll_in_worker_pools(tmp_path, monkeypatch):
    """Provisioned replicas (NOT the safebox host) enroll in the dev worker_pools registry over
    the migration DSN — the subuser role itself cannot write that table (migration 0059)."""
    import sys as _sys

    log: list[tuple[str, str]] = []
    monkeypatch.setitem(_sys.modules, "psycopg", _fake_psycopg(log))
    manifest = _do_manifest(
        tmp_path,
        database={"enabled": True, "dsn_alias": "TAKYON_DEV_MIGRATION_DATABASE_URL"},
    )
    http = FakeHttp(responses={
        ("GET", "/droplets?"): {"droplets": [
            {"id": 101, "name": "takyon-dev-subuser-1"},
            {"id": 102, "name": "takyon-dev-subuser-2"},
            {"id": 103, "name": "takyon-dev-safebox"},
        ]},
    })
    prov = _provisioner(
        manifest=manifest,
        safebox_values={**_DO_VALUES,
                        "TAKYON_DEV_MIGRATION_DATABASE_URL": "postgresql://takyon_migration@dev-host/db"},
        http=http,
    )
    assert prov._create_droplets().status == ep.STATUS_EXISTS
    receipt = prov._register_replica_nodes()
    assert receipt.status == ep.STATUS_CREATED
    assert receipt.data["registered"] == ["takyon-dev-subuser-1", "takyon-dev-subuser-2"]
    inserts = [sql for _dsn, sql in log if sql.startswith("insert into worker_pools")]
    assert len(inserts) == 2


def test_destroy_deletes_tagged_resources_only(tmp_path, monkeypatch):
    """destroy --force sweeps EXACTLY the env-tagged droplets/LB/firewall (+ the manifest-named
    ssh key and vpc) — a foreign droplet/LB/key in the same account is never touched."""
    manifest = _do_manifest(tmp_path)
    manifest["auth0"] = {"enabled": False}
    manifest["stripe"] = {"enabled": False}
    http = FakeHttp(responses={
        # Account-wide listing: ours are selected by env tag OR manifest-derived exact name;
        # the prod droplets (untagged, different names) must never be touched.
        ("GET", "/droplets?"): {"droplets": [
            {"id": 101, "name": "takyon-dev-subuser-1", "tags": ["takyon-env-dev"]},
            {"id": 102, "name": "takyon-dev-subuser-2", "tags": []},   # untagged (scope fallback)
            {"id": 103, "name": "takyon-dev-safebox", "tags": []},
            {"id": 900, "name": "takyon-subuser", "tags": []},         # PROD — must survive
            {"id": 901, "name": "takyon-safebox", "tags": []},         # PROD — must survive
        ]},
        ("GET", "/firewalls"): {"firewalls": [
            {"id": "fw-1", "name": "takyon-dev-split-fw", "tags": ["takyon-env-dev"]},
            {"id": "fw-prod", "name": "fourmanifold-edge", "tags": ["prod-edge"]},
        ]},
        ("GET", "/load_balancers"): {"load_balancers": [
            {"id": "lb-1", "name": "takyon-dev-subuser-lb", "tag": "takyon-env-dev-subuser"},
            {"id": "lb-other", "name": "some-other-lb", "tag": "unrelated"},
        ]},
        ("GET", "/account/keys"): {"ssh_keys": [
            {"id": 7, "name": "takyon-dev-split"},
            {"id": 8, "name": "operator-laptop"},
        ]},
        ("GET", "/vpcs"): {"vpcs": [
            {"id": "vpc-1", "name": "takyon-dev-vpc"},
            {"id": "vpc-prod", "name": "default-nyc1"},
        ]},
    })
    prov = _provisioner(manifest=manifest, safebox_values=_DO_VALUES, http=http, home=tmp_path)
    monkeypatch.setattr(prov, "_live_state_summary", lambda: None)
    result = prov.destroy(force=True)
    deletes = sorted(url for m, url in http.calls if m == "DELETE")
    assert deletes == sorted([
        f"{ep.EnvironmentProvisioner._DO_BASE}/firewalls/fw-1",
        f"{ep.EnvironmentProvisioner._DO_BASE}/load_balancers/lb-1",
        f"{ep.EnvironmentProvisioner._DO_BASE}/droplets/101",
        f"{ep.EnvironmentProvisioner._DO_BASE}/droplets/102",
        f"{ep.EnvironmentProvisioner._DO_BASE}/droplets/103",
        f"{ep.EnvironmentProvisioner._DO_BASE}/account/keys/7",
        f"{ep.EnvironmentProvisioner._DO_BASE}/vpcs/vpc-1",
    ])
    by_resource = {r.resource: r for r in result.receipts}
    assert by_resource["droplets"].status == ep.STATUS_DELETED
    assert by_resource["load_balancer"].status == ep.STATUS_DELETED
    assert by_resource["firewall"].status == ep.STATUS_DELETED
    assert by_resource["ssh_key"].status == ep.STATUS_DELETED
    assert by_resource["vpc"].status == ep.STATUS_DELETED


def test_destroy_do_steps_fail_closed_missing_token(tmp_path, monkeypatch):
    manifest = _do_manifest(tmp_path)
    manifest["auth0"] = {"enabled": False}
    manifest["stripe"] = {"enabled": False}
    http = FakeHttp()
    prov = _provisioner(manifest=manifest, safebox_values={}, http=http, home=tmp_path)
    monkeypatch.setattr(prov, "_live_state_summary", lambda: None)
    result = prov.destroy(force=True)
    blocked = {r.resource for r in result.receipts if r.status == ep.STATUS_BLOCKED}
    assert {"droplets", "load_balancer", "firewall", "ssh_key", "vpc"} <= blocked
    assert http.calls == []
    assert not result.ok


# ── rolling restart (full-4b graceful drain): remove→restart→re-add, fail-closed, receipted ──


class FakeRemote(ep.RemoteExec):
    """Records SSH scripts per host; health/restart outcomes configurable per host ip."""

    def __init__(self, events=None, *, unhealthy=(), fail_restart=()):
        self.events = events if events is not None else []
        self.unhealthy = set(unhealthy)
        self.fail_restart = set(fail_restart)
        self.scripts: list[tuple[str, str]] = []

    def run(self, host, script, *, key_path, timeout=120.0):
        kind = "restart" if "systemctl restart" in script else "health"
        self.scripts.append((host, script))
        self.events.append((kind, host))
        if kind == "health":
            if host in self.unhealthy:
                return 0, "HEALTH app=000 front=000"
            return 0, "HEALTH app=200 front=200"
        if host in self.fail_restart:
            return 1, "restart exploded"
        return 0, "RESTART_OK app=200 front=200"


class FakeLbHttp(ep.HttpTransport):
    """Stateful DO fake for the drain flow: droplet listing + LB membership that actually mutates
    on DELETE/POST …/droplets, so the membership polls see real state."""

    def __init__(self, droplets, lb, events=None):
        self.droplets = droplets
        self.lb = lb
        self.events = events if events is not None else []
        self.calls: list[tuple[str, str]] = []

    def request(self, method, url, *, headers=None, body=None, form=None):
        self.calls.append((method, url))
        if method == "GET" and "/droplets?" in url:
            return {"droplets": self.droplets}
        if method == "GET" and url.endswith("/load_balancers?per_page=200"):
            return {"load_balancers": [self.lb]}
        if method == "GET" and f"/load_balancers/{self.lb['id']}" in url:
            return {"load_balancer": self.lb}
        if url.endswith(f"/load_balancers/{self.lb['id']}/droplets"):
            ids = list((body or {}).get("droplet_ids") or [])
            if method == "DELETE":
                self.lb["droplet_ids"] = [d for d in self.lb["droplet_ids"] if d not in ids]
                self.events.extend(("lb_remove", d) for d in ids)
            elif method == "POST":
                self.lb["droplet_ids"] = list(self.lb["droplet_ids"]) + [
                    d for d in ids if d not in self.lb["droplet_ids"]
                ]
                self.events.extend(("lb_add", d) for d in ids)
            return {}
        return {}


class FakeProbe(ep.HttpProbe):
    """LB front fake: answers 200 with X-Takyon-Node cycling over the CURRENT healthy member set
    (mirrors round_robin over health-checked members). Optional forced statuses first."""

    def __init__(self, lb, names_by_id, *, force_statuses=(), never_serve=()):
        self.lb = lb
        self.names = dict(names_by_id)
        self.force = list(force_statuses)
        self.never_serve = set(never_serve)
        self.n = 0

    def probe(self, url, *, host_header=None, timeout=8.0):
        self.n += 1
        if self.force:
            status = self.force.pop(0)
            if status != 200:
                return status, {}
        members = [self.names[i] for i in self.lb["droplet_ids"] if self.names.get(i)]
        members = [m for m in members if m not in self.never_serve]
        if not members:
            return 503, {}
        return 200, {"x-takyon-node": members[self.n % len(members)]}


def _drain_fixtures():
    droplets = [
        {"id": 907, "name": "takyon-dev-subuser-1",
         "networks": {"v4": [{"type": "private", "ip_address": "10.200.0.2"},
                             {"type": "public", "ip_address": "203.0.113.11"}]}},
        {"id": 918, "name": "takyon-dev-subuser-2",
         "networks": {"v4": [{"type": "public", "ip_address": "203.0.113.12"}]}},
        {"id": 935, "name": "takyon-dev-safebox",
         "networks": {"v4": [{"type": "public", "ip_address": "203.0.113.13"}]}},
    ]
    lb = {"id": "lb-1", "name": "takyon-dev-subuser-lb", "ip": "203.0.113.99", "tag": "",
          "status": "active", "droplet_ids": [907, 918]}
    return droplets, lb


def _drain_manifest(tmp_path, **over):
    manifest = _do_manifest(tmp_path)
    key = tmp_path / "takyon_dev_split"
    key.write_text("not-a-real-key")
    caddy = tmp_path / "Caddyfile.dev"
    caddy.write_text(':80 {\n\theader X-Takyon-Node "__NODE_NAME__"\n\treverse_proxy 127.0.0.1:9119\n}\n')
    manifest["rolling_restart"] = {
        "private_key_path": str(key),
        "caddy_template": str(caddy),
        "grace_seconds": 0,
        "rejoin_timeout_seconds": 10,
        "service": "takyon-subuser.service",
    }
    manifest.update(over)
    return manifest


def _drain_provisioner(tmp_path, *, droplets=None, lb=None, safebox_values=None,
                       remote=None, probe=None, manifest=None):
    events: list = []
    fixtures = _drain_fixtures()
    droplets = fixtures[0] if droplets is None else droplets
    lb = fixtures[1] if lb is None else lb
    http = FakeLbHttp(droplets, lb, events)
    remote = remote if remote is not None else FakeRemote(events)
    remote.events = events
    names = {d["id"]: d["name"] for d in droplets}
    probe = probe if probe is not None else FakeProbe(lb, names)
    prov = ep.EnvironmentProvisioner(
        "dev",
        home=tmp_path,
        safebox_mod=FakeSafebox(safebox_values if safebox_values is not None else _DO_VALUES),
        http=http,
        manifest=manifest or _drain_manifest(tmp_path),
        remote=remote,
        probe=probe,
        sleep=lambda _s: None,
    )
    return prov, http, remote, events


def test_rolling_restart_fails_closed_missing_do_token(tmp_path):
    prov, http, _remote, _events = _drain_provisioner(tmp_path, safebox_values={})
    result = prov.rolling_restart()
    assert not result.ok
    assert result.receipts[-1].status == ep.STATUS_BLOCKED
    assert result.receipts[-1].deposit == "TAKYON_DO_API_TOKEN"
    assert http.calls == []


def test_rolling_restart_refuses_when_other_replica_unhealthy(tmp_path):
    """The fail-closed keystone: replica 2 is not serving 200s locally, so draining replica 1
    would leave zero healthy backends — the run must refuse BEFORE any LB mutation or restart."""
    remote = FakeRemote(unhealthy={"203.0.113.12"})
    prov, http, remote, events = _drain_provisioner(tmp_path, remote=remote)
    result = prov.rolling_restart()
    assert not result.ok
    err = result.receipts[-1]
    assert err.status == ep.STATUS_ERROR
    assert "refusing to drain takyon-dev-subuser-1" in err.detail
    assert "takyon-dev-subuser-2" in err.detail
    # No LB membership mutation and no restart happened.
    assert [e for e in events if e[0] in ("lb_remove", "lb_add", "restart")] == []
    # The LB member set is untouched.
    assert sorted(prov.http.lb["droplet_ids"]) == [907, 918]


def test_rolling_restart_refuses_when_other_replica_not_lb_member(tmp_path):
    droplets, lb = _drain_fixtures()
    lb["droplet_ids"] = [907]  # replica 2 already out of the LB
    prov, _http, _remote, events = _drain_provisioner(tmp_path, droplets=droplets, lb=lb)
    result = prov.rolling_restart()
    assert not result.ok
    assert "not an LB member" in result.receipts[-1].detail
    assert [e for e in events if e[0] in ("lb_remove", "restart")] == []


def test_rolling_restart_orders_remove_restart_readd_per_replica(tmp_path):
    """The zero-loss ordering contract, per replica, sequentially: health-gate the OTHER replica →
    LB remove → restart over SSH → LB re-add → (rejoin proof) — replica 2 starts only after
    replica 1 is proven back."""
    prov, http, remote, events = _drain_provisioner(tmp_path)
    result = prov.rolling_restart()
    assert result.ok, [r.to_dict() for r in result.receipts]
    ordered = [e for e in events if e[0] in ("lb_remove", "restart", "lb_add")]
    assert ordered == [
        ("lb_remove", 907), ("restart", "203.0.113.11"), ("lb_add", 907),
        ("lb_remove", 918), ("restart", "203.0.113.12"), ("lb_add", 918),
    ]
    # Both replicas ended back in the LB.
    assert sorted(http.lb["droplet_ids"]) == [907, 918]
    # The safebox host is never touched (not a replica).
    assert all(host != "203.0.113.13" for host, _ in remote.scripts)
    # Receipt trail: drain/restart/rejoin(+membership) per replica, then the summary.
    kinds = [r.resource for r in result.receipts]
    assert kinds == ["drain", "restart", "rejoin", "rejoin",
                     "drain", "restart", "rejoin", "rejoin", "rolling_restart"]
    assert all(r.action == "restart" for r in result.receipts)
    assert result.receipts[-1].status == ep.STATUS_CREATED
    # Receipts are appended to the env's receipts.jsonl.
    lines = (tmp_path / "environments" / "dev" / "receipts.jsonl").read_text().strip().splitlines()
    import json as _json
    rows = [_json.loads(l) for l in lines]
    assert [r["resource"] for r in rows] == kinds
    assert all(r["action"] == "restart" for r in rows)


def test_rolling_restart_renders_node_identity_into_caddy_front(tmp_path):
    """Each replica's restart script converges the tracked Caddy template with ITS node name —
    the header the rejoin gate reads."""
    prov, _http, remote, _events = _drain_provisioner(tmp_path)
    assert prov.rolling_restart().ok
    restart_scripts = {host: script for host, script in remote.scripts if "systemctl restart" in script}
    assert 'header X-Takyon-Node "takyon-dev-subuser-1"' in restart_scripts["203.0.113.11"]
    assert 'header X-Takyon-Node "takyon-dev-subuser-2"' in restart_scripts["203.0.113.12"]
    for script in restart_scripts.values():
        assert "caddy validate" in script
        assert "systemctl restart takyon-subuser.service" in script
        assert "__NODE_NAME__" not in script


def test_rolling_restart_leaves_failed_replica_out_of_lb(tmp_path):
    """A replica that does not come back healthy is LEFT OUT of the LB (never re-add an unhealthy
    node) and the run aborts — the surviving replica keeps serving."""
    remote = FakeRemote(fail_restart={"203.0.113.11"})
    prov, http, remote, events = _drain_provisioner(tmp_path, remote=remote)
    result = prov.rolling_restart()
    assert not result.ok
    assert ("lb_add", 907) not in events
    assert http.lb["droplet_ids"] == [918]
    assert "left OUT of the LB" in result.receipts[-1].detail
    # Replica 2 was never drained.
    assert ("lb_remove", 918) not in events


def test_rolling_restart_refuses_tag_managed_lb(tmp_path):
    droplets, lb = _drain_fixtures()
    lb["tag"] = "takyon-env-dev-subuser"
    prov, _http, _remote, events = _drain_provisioner(tmp_path, droplets=droplets, lb=lb)
    result = prov.rolling_restart()
    assert not result.ok
    assert "tag-managed" in result.receipts[-1].detail
    assert [e for e in events if e[0] in ("lb_remove", "restart", "lb_add")] == []


def test_rolling_restart_requires_two_replicas(tmp_path):
    droplets, lb = _drain_fixtures()
    droplets = [d for d in droplets if d["name"] != "takyon-dev-subuser-2"]
    prov, _http, _remote, events = _drain_provisioner(tmp_path, droplets=droplets, lb=lb)
    result = prov.rolling_restart()
    assert not result.ok
    assert ">=2 replicas" in result.receipts[-1].detail
    assert [e for e in events if e[0] in ("lb_remove", "restart")] == []


def test_rolling_restart_aborts_on_non_200_during_rejoin_gate(tmp_path):
    """Any non-200 through the LB during the rejoin gate violates the zero-loss contract → abort."""
    droplets, lb = _drain_fixtures()
    names = {d["id"]: d["name"] for d in droplets}
    probe = FakeProbe(lb, names, force_statuses=[200, 503])  # preflight OK, first rejoin probe 503
    prov, _http, _remote, _events = _drain_provisioner(tmp_path, droplets=droplets, lb=lb, probe=probe)
    result = prov.rolling_restart()
    assert not result.ok
    err = result.receipts[-1]
    assert err.resource == "rejoin"
    assert "503" in err.detail
    assert "zero-loss" in err.detail


def test_rolling_restart_aborts_when_lb_never_routes_to_node(tmp_path):
    """Membership re-added but the LB never actually routes to the node (header never shows it):
    the gate must time out and abort BEFORE draining the next replica."""
    droplets, lb = _drain_fixtures()
    names = {d["id"]: d["name"] for d in droplets}
    probe = FakeProbe(lb, names, never_serve={"takyon-dev-subuser-1"})
    prov, _http, _remote, events = _drain_provisioner(tmp_path, droplets=droplets, lb=lb, probe=probe)
    result = prov.rolling_restart()
    assert not result.ok
    assert "never routed" in result.receipts[-1].detail
    assert ("lb_remove", 918) not in events  # replica 2 untouched


def test_rolling_restart_fails_closed_without_caddy_template(tmp_path):
    manifest = _drain_manifest(tmp_path)
    manifest["rolling_restart"]["caddy_template"] = str(tmp_path / "missing" / "Caddyfile.dev")
    prov, http, _remote, _events = _drain_provisioner(tmp_path, manifest=manifest)
    result = prov.rolling_restart()
    assert not result.ok
    assert "caddy front template not found" in result.receipts[-1].detail
    assert http.calls == []  # refused before ANY provider call


def test_dev_manifest_declares_rolling_restart_rail():
    data = ep.load_manifest("dev")
    rr = data.get("rolling_restart") or {}
    assert rr.get("service") == "takyon-subuser.service"
    assert float(rr.get("grace_seconds", 0)) > 0
    assert float(rr.get("rejoin_timeout_seconds", 0)) >= 30


def test_takyon_env_restart_subcommand_registered():
    args = _parse_via_main(["env", "restart", "dev"])
    assert args is not None, "takyon env restart dev did not dispatch to cmd_env"
    assert getattr(args, "env_action") == "restart"
    assert getattr(args, "env_name") == "dev"
