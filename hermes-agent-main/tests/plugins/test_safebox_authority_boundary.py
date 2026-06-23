"""Authority-boundary regression tests (GOAL_RULES §0).

Locks the two surgical fixes from the threat-model audit:
  G1 — /v1/env is an infra ALLOWLIST (deny-by-default); the safebox's own authority secrets
       (TAKYON_CAP_SIGNING_KEY, TAKYON_SAFEBOX_TOKEN) are never vended OR overwritten over /v1/env.
  G2 — the operator proxy authorizes on a CAPABILITY only; the bare shared TAKYON_SAFEBOX_TOKEN is
       transport reachability, not spend authority, and is refused (401).
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from plugins.takyon import core, safebox_provider_proxy
from plugins.takyon.safebox_app import build_safebox_app


# ── G1: egress allowlist policy (core, pure) ──────────────────────────────────────────────────────
def test_env_egress_denies_self_authority_secrets():
    assert core.env_egress_allowed("TAKYON_CAP_SIGNING_KEY") is False
    assert core.env_egress_allowed("TAKYON_SAFEBOX_TOKEN") is False
    names = core.safebox_self_authority_secret_names()
    assert "TAKYON_CAP_SIGNING_KEY" in names and "TAKYON_SAFEBOX_TOKEN" in names


def test_env_egress_denies_provider_keys():
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
              "TAVILY_API_KEY", "FAL_KEY", "COMPOSIO_API_KEY"):
        assert core.env_egress_allowed(k) is False, k


def test_env_egress_admits_infra_only():
    for k in ("DATABASE_URL", "POSTGRES_URL", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
              "AUTH0_CLIENT_SECRET", "AUTH0_DOMAIN", "SUPABASE_S3_SECRET_ACCESS_KEY",
              "R2_S3_SECRET_ACCESS_KEY", "UMAMI_API_KEY", "VERCEL_TOKEN", "CLOUDFLARE_API_TOKEN",
              "TAKYON_GSC_SERVICE_ACCOUNT_KEY"):
        assert core.env_egress_allowed(k) is True, k


def test_env_egress_denies_safebox_verification_secrets():
    # Authority-equivalent secrets the safebox uses itself and NO runtime fetches: never vended,
    # not over-admitted by a broad prefix (the audited STRIPE_/SUPABASE_ prefix hole).
    for k in ("STRIPE_BILLING_WEBHOOK_SECRET", "SUPABASE_SERVICE_ROLE_KEY"):
        assert core.env_egress_allowed(k) is False, k
        assert k in core.safebox_self_authority_secret_names(), k


def test_env_egress_admits_un_regressed_names():
    # Names the first (too-narrow) allowlist wrongly denied — must be fetchable again (no regression).
    for k in ("TAKYON_DASHBOARD_SESSION_TOKEN", "OPENMETER_API_TOKEN", "TAKYON_OPENMETER_API_TOKEN",
              "OPENMETER_URL", "TAKYON_OPENMETER_URL", "OPENMETER_API_URL"):
        assert core.env_egress_allowed(k) is True, k


def test_env_egress_denies_unknown_by_default():
    # The anti-G1 property: a name nobody allowlisted is NOT vendable (a denylist would have leaked it).
    for k in ("SOME_NEW_SECRET", "TAKYON_SECRET_SAUCE", "ADMIN_PASSWORD", "JWT_PRIVATE_KEY", ""):
        assert core.env_egress_allowed(k) is False, k


# ── G1: /v1/env HTTP routes ───────────────────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch):
    # Run as the safebox host itself so env-backed values resolve LOCALLY (no remote authority needed).
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "test-token")
    monkeypatch.setenv("TAKYON_CAP_SIGNING_KEY", "test-signing-key-value")
    monkeypatch.setenv("DATABASE_URL", "postgres://example/db")
    return TestClient(build_safebox_app())


_AUTH = {"Authorization": "Bearer test-token"}


def test_read_route_refuses_authority_secrets(client):
    for k in ("TAKYON_CAP_SIGNING_KEY", "TAKYON_SAFEBOX_TOKEN"):
        r = client.get(f"/v1/env/{k}", headers=_AUTH)
        assert r.status_code == 404, (k, r.status_code, r.text)
        assert "test-signing-key-value" not in r.text


def test_first_route_refuses_when_only_denied_keys(client):
    r = client.post("/v1/env/first",
                    json={"keys": ["TAKYON_CAP_SIGNING_KEY", "ANTHROPIC_API_KEY"]}, headers=_AUTH)
    assert r.status_code == 404


def test_read_route_serves_infra_secret(client):
    r = client.get("/v1/env/DATABASE_URL", headers=_AUTH)
    assert r.status_code == 200 and r.json()["value"] == "postgres://example/db"


def test_write_and_delete_routes_refuse_sensitive_keys(client):
    # No runtime plane writes env over HTTP: self-authority secrets, infra (DATABASE_URL clobber/DoS),
    # and provider keys (the swap the safebox's own proxies would resolve) are all 403. Lowercase
    # variants 403 too (not the old 500), so case can't sneak a write past.
    for k in ("TAKYON_CAP_SIGNING_KEY", "TAKYON_SAFEBOX_TOKEN", "DATABASE_URL", "STRIPE_SECRET_KEY",
              "ANTHROPIC_API_KEY", "STRIPE_BILLING_WEBHOOK_SECRET",
              "takyon_cap_signing_key", "database_url"):
        assert client.post(f"/v1/env/{k}", json={"value": "attacker"},
                           headers=_AUTH).status_code == 403, k
    assert client.delete("/v1/env/DATABASE_URL", headers=_AUTH).status_code == 403
    assert client.delete("/v1/env/TAKYON_SAFEBOX_TOKEN", headers=_AUTH).status_code == 403
    # The infra secret still READS fine — only the WRITE is closed.
    assert client.get("/v1/env/DATABASE_URL", headers=_AUTH).status_code == 200


def test_list_route_never_advertises_authority_secrets(client):
    r = client.get("/v1/env", headers=_AUTH)
    assert "TAKYON_CAP_SIGNING_KEY" not in r.text and "TAKYON_SAFEBOX_TOKEN" not in r.text


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
