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
    """Records requests; returns queued responses keyed by (METHOD, url-substring)."""

    def __init__(self, responses: dict[tuple[str, str], object] | None = None):
        self.responses = dict(responses or {})
        self.calls: list[tuple[str, str]] = []

    def request(self, method, url, *, headers=None, body=None, form=None):
        self.calls.append((method, url))
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
    for key in ("domains", "database", "safebox", "auth0", "cloudflare", "stripe", "droplet", "plans"):
        assert key in data, f"dev.yaml missing {key}"
    # The DB step targets a dev migration DSN by ALIAS, never a literal.
    assert data["database"]["dsn_alias"] == "TAKYON_DEV_MIGRATION_DATABASE_URL"
    assert data["database"]["enabled"] is True
    # dev domains must be *.dev / localtest, never the prod company base or dashboard host.
    assert data["domains"]["company_base"] == "dev.coscale.app"
    # Auth0 mgmt token alias is the one-time-deposit credential named in the plan.
    assert data["auth0"]["mgmt_token_alias"] == "TAKYON_AUTH0_MGMT_TOKEN"


def test_hermetic_manifest_all_disabled():
    data = ep.load_manifest("hermetic")
    assert data["name"] == "hermetic"
    for twin in ("database", "safebox", "auth0", "cloudflare", "stripe", "droplet"):
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


def test_create_auth0_fails_closed_naming_mgmt_token():
    manifest = {
        "name": "dev",
        "domains": {"company_base": "dev.coscale.app"},
        "database": {"enabled": False},
        "safebox": {"enabled": False},
        "auth0": {"enabled": True, "mgmt_token_alias": "TAKYON_AUTH0_MGMT_TOKEN", "domain_alias": "AUTH0_DOMAIN"},
    }
    http = FakeHttp()
    prov = _provisioner(manifest=manifest, safebox_values={}, http=http)
    receipt = prov._create_auth0()
    assert receipt.status == ep.STATUS_BLOCKED
    assert receipt.deposit == "TAKYON_AUTH0_MGMT_TOKEN"
    assert "TAKYON_AUTH0_MGMT_TOKEN" in receipt.detail
    assert http.calls == []


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
        "stripe": {"enabled": True, "webhook_url": "http://localhost:9119/api/webhooks/stripe",
                   "enabled_events": ["checkout.session.completed"]},
    }
    http = FakeHttp(responses={
        ("GET", "/webhook_endpoints"): {"data": [{"id": "we_1", "url": "http://localhost:9119/api/webhooks/stripe"}]},
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
