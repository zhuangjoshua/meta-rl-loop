"""Authority-boundary regression tests (GOAL_RULES §0).

Locks the two surgical fixes from the threat-model audit:
  G1 — /v1/env is an infra ALLOWLIST (deny-by-default); the safebox's own authority secrets
       (TAKYON_CAP_SIGNING_KEY, TAKYON_SAFEBOX_TOKEN, TAKYON_SAFEBOX_OPERATOR_TOKEN) are never
       vended OR overwritten over /v1/env.
  G2 — the operator proxy authorizes on a CAPABILITY only; the bare shared TAKYON_SAFEBOX_TOKEN is
       transport reachability, not spend authority, and is refused (401).
"""
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from plugins.takyon import core, ledger_gate, safebox_provider_proxy
from plugins.takyon.safebox_app import build_safebox_app


# ── G1: egress allowlist policy (core, pure) ──────────────────────────────────────────────────────
def test_env_egress_denies_self_authority_secrets():
    assert core.env_egress_allowed("TAKYON_CAP_SIGNING_KEY") is False
    assert core.env_egress_allowed("TAKYON_SAFEBOX_OPERATOR_TOKEN") is False
    assert core.env_egress_allowed("TAKYON_SAFEBOX_TOKEN") is False
    names = core.safebox_self_authority_secret_names()
    assert "TAKYON_CAP_SIGNING_KEY" in names
    assert "TAKYON_SAFEBOX_OPERATOR_TOKEN" in names
    assert "TAKYON_SAFEBOX_TOKEN" in names


def test_env_egress_denies_provider_keys():
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
              "TAVILY_API_KEY", "FAL_KEY", "COMPOSIO_API_KEY"):
        assert core.env_egress_allowed(k) is False, k


def test_env_egress_admits_public_config_only():
    for k in ("AUTH0_DOMAIN", "AUTH0_CLIENT_ID", "SUPABASE_URL", "SUPABASE_S3_ENDPOINT",
              "SUPABASE_S3_REGION", "POSTMARK_FROM_EMAIL", "VERCEL_PROJECT_ID",
              "VERCEL_TEAM_ID", "CLOUDFLARE_ZONE_NAME", "TAKYON_PRODUCT_EDGE_WORKER",
              "TAKYON_STORAGE_BUCKET", "OPENMETER_URL", "TAKYON_OPENMETER_URL",
              "OPENMETER_API_URL"):
        assert core.env_egress_allowed(k) is True, k


def test_env_egress_denies_database_authority_names():
    for k in (
        "DATABASE_URL",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
        "POSTGRES_URL_NON_POOLING",
        "TAKYON_OPERATOR_DATABASE_URL",
        "TAKYON_APP_DATABASE_URL",
        "TAKYON_SAFEBOX_DATABASE_URL",
        "TAKYON_MIGRATION_DATABASE_URL",
        "MIGRATION_DATABASE_URL",
    ):
        assert core.env_egress_allowed(k) is False, k


def test_env_egress_denies_residual_authority_secrets():
    # These used to be the remaining authority-equivalent vends. Runtime planes now call safebox
    # action routes for payment, email, object store, deploy/domain, edge mutation, reporting, and
    # Search Console instead. /v1/env is public config only.
    for k in (
        "STRIPE_SECRET_KEY",
        "POSTMARK_SERVER_TOKEN",
        "SUPABASE_S3_ACCESS_KEY_ID",
        "SUPABASE_S3_SECRET_ACCESS_KEY",
        "R2_S3_ACCESS_KEY_ID",
        "R2_S3_SECRET_ACCESS_KEY",
        "CLOUDFLARE_API_TOKEN",
        "VERCEL_TOKEN",
        "TAKYON_GSC_SERVICE_ACCOUNT_KEY",
        "UMAMI_API_KEY",
        "TAKYON_DASHBOARD_SESSION_TOKEN",
        "OPENMETER_API_TOKEN",
        "TAKYON_OPENMETER_API_TOKEN",
    ):
        assert core.env_egress_allowed(k) is False, k


def test_env_egress_denies_auth0_authority_secrets():
    # Dashboard OAuth exchange + cookie signing now happen on the safebox. The runtime only needs
    # public Auth0 domain/client id; these authority-equivalent secrets must not vend over /v1/env.
    for k in ("AUTH0_CLIENT_SECRET", "AUTH0_SECRET"):
        assert core.env_egress_allowed(k) is False, k


def test_env_egress_denies_app_webhook_secret():
    # STRIPE_WEBHOOK_SECRET is no longer fetched by any runtime plane: the sub-user (flow-B) app
    # webhook is verified server-side on the safebox (/v1/stripe/app-webhook/verify), mirroring the
    # flow-A billing webhook. The signing secret must never leave the safebox over /v1/env.
    assert core.env_egress_allowed("STRIPE_WEBHOOK_SECRET") is False


def test_env_egress_denies_safebox_verification_secrets():
    # Authority-equivalent secrets the safebox uses itself and NO runtime fetches: never vended,
    # not over-admitted by a broad prefix (the audited STRIPE_/SUPABASE_ prefix hole).
    for k in ("STRIPE_BILLING_WEBHOOK_SECRET", "SUPABASE_SERVICE_ROLE_KEY"):
        assert core.env_egress_allowed(k) is False, k
        assert k in core.safebox_self_authority_secret_names(), k


class _FakeGateResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeGateCursor:
    def __init__(self, conn):
        self.conn = conn
        self._row = None

    def execute(self, sql, params=None):
        self.conn.statements.append((sql, params))
        self._row = ("prior-bypass",) if "current_setting" in sql else None
        return self

    def fetchone(self):
        return self._row

    def close(self):
        self.conn.closed = True


class _FakeGateConn:
    def __init__(self, *, session_user: str = "takyon_safebox_authority", current_user: str = "takyon_safebox_authority"):
        self.statements = []
        self.closed = False
        self.session_user = session_user
        self.current_user = current_user

    def cursor(self):
        return _FakeGateCursor(self)

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        normalized = str(sql or "").lower()
        if "session_user::text" in normalized and "current_user::text" in normalized:
            return _FakeGateResult({"session_user": self.session_user, "current_user": self.current_user})
        return _FakeGateResult(("gate-ok",))


def test_ledger_gate_requires_safebox_database_role(monkeypatch):
    monkeypatch.delenv("TAKYON_ALLOW_LEGACY_DB_ROLES", raising=False)
    conn = _FakeGateConn(
        session_user="takyon_operator_runtime",
        current_user="takyon_operator_runtime",
    )

    with pytest.raises(RuntimeError, match="Safebox authority database login"):
        ledger_gate.gate_fetchone(conn, "select * from safebox_billing_reserve(%s)", ("x",))

    sql = [s.lower() for s, _ in conn.statements]
    assert "set role takyon_runtime" not in sql
    assert "reset role" not in sql


def test_ledger_gate_has_no_legacy_role_demotion_opt_in():
    src = Path(ledger_gate.__file__).read_text(encoding="utf-8").lower()
    assert "takyon_allow_legacy_ledger_role_demotion" not in src
    assert "set role" not in src
    assert "reset role" not in src


def test_ledger_gate_keeps_safebox_authority_connection_owner(monkeypatch):
    monkeypatch.delenv("TAKYON_ALLOW_LEGACY_DB_ROLES", raising=False)
    conn = _FakeGateConn()

    assert ledger_gate.gate_fetchone(conn, "select * from safebox_billing_reserve(%s)", ("x",)) == (
        "gate-ok",
    )

    sql = [s.lower() for s, _ in conn.statements]
    assert "set role takyon_runtime" not in sql
    assert "reset role" not in sql
    assert "select set_config('takyon.rls_bypass', '0', false)" in sql
    assert ("select set_config('takyon.rls_bypass', %s, false)", ("prior-bypass",)) in conn.statements


def test_env_egress_keeps_openmeter_endpoints_without_tokens():
    # OpenMeter can remain a fail-soft mirror endpoint, but its bearer token is not env-vendable.
    for k in ("OPENMETER_URL", "TAKYON_OPENMETER_URL", "OPENMETER_API_URL"):
        assert core.env_egress_allowed(k) is True, k
    for k in ("OPENMETER_API_TOKEN", "TAKYON_OPENMETER_API_TOKEN"):
        assert core.env_egress_allowed(k) is False, k


def test_env_egress_denies_unknown_by_default():
    # The anti-G1 property: a name nobody allowlisted is NOT vendable (a denylist would have leaked it).
    for k in ("SOME_NEW_SECRET", "TAKYON_SECRET_SAUCE", "ADMIN_PASSWORD", "JWT_PRIVATE_KEY", "",
              # SUPABASE_JWT_SECRET dropped from the allowlist — product-JWT verification is now
              # server-side (alg-confusion fix), so the symmetric secret is never fetched or vended.
              "SUPABASE_JWT_SECRET"):
        assert core.env_egress_allowed(k) is False, k


# ── G1: /v1/env HTTP routes ───────────────────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch):
    # Run as the safebox host itself so env-backed values resolve LOCALLY (no remote authority needed).
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "test-token")
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_TOKEN", "operator-route-token-value")
    monkeypatch.setenv("TAKYON_CAP_SIGNING_KEY", "test-signing-key-value")
    monkeypatch.setenv("AUTH0_CLIENT_SECRET", "auth0-client-secret-value")
    monkeypatch.setenv("AUTH0_SECRET", "auth0-cookie-secret-value")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "stripe-secret-value")
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "postmark-token-value")
    monkeypatch.setenv("SUPABASE_S3_ACCESS_KEY_ID", "supabase-s3-access-key-id")
    monkeypatch.setenv("SUPABASE_S3_SECRET_ACCESS_KEY", "supabase-s3-secret-value")
    monkeypatch.setenv("R2_S3_ACCESS_KEY_ID", "r2-s3-access-key-id")
    monkeypatch.setenv("R2_S3_SECRET_ACCESS_KEY", "r2-s3-secret-value")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cloudflare-token-value")
    monkeypatch.setenv("VERCEL_TOKEN", "vercel-token-value")
    monkeypatch.setenv("TAKYON_GSC_SERVICE_ACCOUNT_KEY", "gsc-service-account-json-value")
    monkeypatch.setenv("UMAMI_API_KEY", "umami-api-key-value")
    monkeypatch.setenv("TAKYON_DASHBOARD_SESSION_TOKEN", "dashboard-session-token-value")
    monkeypatch.setenv("OPENMETER_API_TOKEN", "openmeter-api-token-value")
    monkeypatch.setenv("TAKYON_OPENMETER_API_TOKEN", "takyon-openmeter-api-token-value")
    monkeypatch.setenv("DATABASE_URL", "postgres://example/db")
    monkeypatch.setenv("TAKYON_APP_DATABASE_URL", "postgres://app/db")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    return TestClient(build_safebox_app())


_AUTH = {"Authorization": "Bearer test-token"}


def test_read_route_refuses_authority_secrets(client):
    for k in (
        "TAKYON_CAP_SIGNING_KEY",
        "TAKYON_SAFEBOX_OPERATOR_TOKEN",
        "TAKYON_SAFEBOX_TOKEN",
        "AUTH0_CLIENT_SECRET",
        "AUTH0_SECRET",
        "STRIPE_SECRET_KEY",
        "POSTMARK_SERVER_TOKEN",
        "SUPABASE_S3_ACCESS_KEY_ID",
        "SUPABASE_S3_SECRET_ACCESS_KEY",
        "R2_S3_ACCESS_KEY_ID",
        "R2_S3_SECRET_ACCESS_KEY",
        "CLOUDFLARE_API_TOKEN",
        "VERCEL_TOKEN",
        "TAKYON_GSC_SERVICE_ACCOUNT_KEY",
        "UMAMI_API_KEY",
        "TAKYON_DASHBOARD_SESSION_TOKEN",
        "OPENMETER_API_TOKEN",
        "TAKYON_OPENMETER_API_TOKEN",
    ):
        r = client.get(f"/v1/env/{k}", headers=_AUTH)
        assert r.status_code == 404, (k, r.status_code, r.text)
        assert "test-signing-key-value" not in r.text
        assert "operator-route-token-value" not in r.text
        assert "auth0-client-secret-value" not in r.text
        assert "auth0-cookie-secret-value" not in r.text
        assert "stripe-secret-value" not in r.text
        assert "postmark-token-value" not in r.text
        assert "supabase-s3-access-key-id" not in r.text
        assert "supabase-s3-secret-value" not in r.text
        assert "r2-s3-access-key-id" not in r.text
        assert "r2-s3-secret-value" not in r.text
        assert "cloudflare-token-value" not in r.text
        assert "vercel-token-value" not in r.text
        assert "gsc-service-account-json-value" not in r.text
        assert "umami-api-key-value" not in r.text
        assert "dashboard-session-token-value" not in r.text
        assert "openmeter-api-token-value" not in r.text
        assert "takyon-openmeter-api-token-value" not in r.text


def test_first_route_refuses_when_only_denied_keys(client):
    r = client.post("/v1/env/first",
                    json={"keys": ["TAKYON_CAP_SIGNING_KEY", "ANTHROPIC_API_KEY"]}, headers=_AUTH)
    assert r.status_code == 404


def test_read_route_serves_public_infra_secret(client):
    r = client.get("/v1/env/SUPABASE_URL", headers=_AUTH)
    assert r.status_code == 200 and r.json()["value"] == "https://example.supabase.co"


def test_database_egress_is_not_vendable(client):
    for key in ("DATABASE_URL", "TAKYON_APP_DATABASE_URL"):
        r = client.get(f"/v1/env/{key}", headers=_AUTH)
        assert r.status_code == 404, key

    first = client.post(
        "/v1/env/first",
        json={"keys": ["DATABASE_URL", "TAKYON_APP_DATABASE_URL"]},
        headers=_AUTH,
    )
    assert first.status_code == 404

    snapshot = client.get("/v1/env/snapshot", headers=_AUTH)
    assert snapshot.status_code == 200
    assert "DATABASE_URL" not in snapshot.json()["snapshot"]
    assert "TAKYON_APP_DATABASE_URL" not in snapshot.json()["snapshot"]


def test_first_route_keeps_public_infra_fallback(client):
    first = client.post(
        "/v1/env/first",
        json={"keys": ["SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL"]},
        headers=_AUTH,
    )
    assert first.status_code == 200 and first.json()["value"] == "https://example.supabase.co"


def test_write_and_delete_routes_refuse_sensitive_keys(client):
    # No runtime plane writes env over HTTP: self-authority secrets, DB authority clobber/DoS,
    # and provider keys (the swap the safebox's own proxies would resolve) are all 403. Lowercase
    # variants 403 too (not the old 500), so case can't sneak a write past.
    for k in ("TAKYON_CAP_SIGNING_KEY", "TAKYON_SAFEBOX_OPERATOR_TOKEN", "TAKYON_SAFEBOX_TOKEN", "DATABASE_URL",
              "TAKYON_APP_DATABASE_URL", "STRIPE_SECRET_KEY",
              "ANTHROPIC_API_KEY", "STRIPE_BILLING_WEBHOOK_SECRET",
              "takyon_cap_signing_key", "takyon_safebox_operator_token", "database_url"):
        assert client.post(f"/v1/env/{k}", json={"value": "attacker"},
                           headers=_AUTH).status_code == 403, k
    assert client.delete("/v1/env/DATABASE_URL", headers=_AUTH).status_code == 403
    assert client.delete("/v1/env/TAKYON_APP_DATABASE_URL", headers=_AUTH).status_code == 403
    assert client.delete("/v1/env/TAKYON_SAFEBOX_OPERATOR_TOKEN", headers=_AUTH).status_code == 403
    assert client.delete("/v1/env/TAKYON_SAFEBOX_TOKEN", headers=_AUTH).status_code == 403


def test_shared_token_alone_cannot_mutate_public_env_config(client):
    saved = client.post("/v1/env/SUPABASE_URL", json={"value": "https://attacker.example"}, headers=_AUTH)
    assert saved.status_code == 403
    assert saved.json()["detail"] == "env_write_forbidden"
    assert client.get("/v1/env/SUPABASE_URL", headers=_AUTH).json()["value"] == "https://example.supabase.co"

    deleted = client.delete("/v1/env/SUPABASE_URL", headers=_AUTH)
    assert deleted.status_code == 403
    assert deleted.json()["detail"] == "env_write_forbidden"
    assert client.get("/v1/env/SUPABASE_URL", headers=_AUTH).json()["value"] == "https://example.supabase.co"


def test_list_route_never_advertises_authority_secrets(client):
    r = client.get("/v1/env", headers=_AUTH)
    assert "TAKYON_CAP_SIGNING_KEY" not in r.text
    assert "TAKYON_SAFEBOX_OPERATOR_TOKEN" not in r.text
    assert "TAKYON_SAFEBOX_TOKEN" not in r.text


# ── G2: operator proxy requires a capability; bare token refused ────────────────────────────────────
def test_operator_proxy_refuses_bare_internal_token(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "test-token")
    monkeypatch.setenv("TAKYON_CAP_SIGNING_KEY", "test-signing-key-value")
    # Even with a platform-operator id configured, the bare token must NOT buy spend authority.
    monkeypatch.setenv("TAKYON_PLATFORM_OPERATOR_USER_ID", "op-1")
    with pytest.raises(HTTPException) as ei:
        safebox_provider_proxy._authorize_operator_proxy(
            "Bearer test-token", None, capability_audiences=frozenset({"anthropic.messages"})
        )
    assert ei.value.status_code == 401


def test_operator_proxy_refuses_nothing_presented(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "test-token")
    monkeypatch.setenv("TAKYON_CAP_SIGNING_KEY", "test-signing-key-value")
    with pytest.raises(HTTPException) as ei:
        safebox_provider_proxy._authorize_operator_proxy(
            None, None, capability_audiences=frozenset({"anthropic.messages"})
        )
    assert ei.value.status_code == 401
