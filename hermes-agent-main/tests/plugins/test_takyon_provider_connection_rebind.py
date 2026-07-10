from __future__ import annotations

import contextlib

from starlette.testclient import TestClient

from plugins.takyon import egress_gateway, money_shape, safebox, safebox_app


_TRANSPORT_TOKEN = "transport-only-test-token"
_OPERATOR_TOKEN = "operator-only-test-token"
_BUSINESS = "repopulse-e2e"
_CONNECTION = "gh-repos"
_APPROVAL_ID = "approval-exact-1"
_CONNECTION_ID = "connection-1"
_SECRET = "github_pat_NEVER_RETURN_THIS"


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _ConnectionDb:
    def __init__(self, *, approval_digest: str, ciphertext: bytes, nonce: bytes, fingerprint: str):
        self.approval_digest = approval_digest
        self.ciphertext = ciphertext
        self.nonce = nonce
        self.fingerprint = fingerprint
        self.rebound: tuple[str, str] | None = None

    @contextlib.contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params=None):
        statement = " ".join(str(sql).split())
        if "set_config('takyon.rls_bypass'" in statement:
            return _Cursor(("1",))
        if "from provider_connections" in statement and "for update" in statement:
            return _Cursor(
                (
                    _CONNECTION_ID,
                    _CONNECTION,
                    "github",
                    "api.github.com",
                    "/repos",
                    ["GET"],
                    {"type": "header", "name": "Authorization"},
                    "business",
                    _APPROVAL_ID,
                    self.ciphertext,
                    self.nonce,
                    self.fingerprint,
                )
            )
        if "from operator_approvals" in statement:
            assert params[0] == _BUSINESS
            assert params[1] == _APPROVAL_ID
            return _Cursor(("approved",) if params[2] == self.approval_digest else None)
        if statement.startswith("update provider_connections set approved_scope_digest"):
            self.rebound = (str(params[0]), str(params[1]))
            return _Cursor((_CONNECTION_ID,))
        raise AssertionError(f"unexpected SQL: {statement}")


def _scope() -> dict:
    return egress_gateway.normalize_connection_scope(
        provider_kind="github",
        allowed_host="api.github.com",
        allowed_path_prefix="/repos",
        allowed_methods=["GET"],
        placement={"type": "header", "name": "Authorization"},
        scope="business",
    )


def _exact_approval_digest() -> str:
    return money_shape.payload_digest(
        egress_gateway.connection_approval_payload(_CONNECTION, _scope())
    )


def _client(monkeypatch, conn: _ConnectionDb) -> TestClient:
    monkeypatch.setenv(safebox_app._SAFEBOX_TOKEN_ENV, _TRANSPORT_TOKEN)
    monkeypatch.setenv(safebox_app._OPERATOR_TOKEN_ENV, _OPERATOR_TOKEN)
    monkeypatch.setenv(safebox_app._OPERATOR_CLIENTS_ENV, "testclient")

    @contextlib.contextmanager
    def _fake_db():
        yield conn

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_db)
    return TestClient(safebox_app.build_safebox_app())


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_TRANSPORT_TOKEN}",
        "X-Takyon-Operator-Token": _OPERATOR_TOKEN,
    }


def _sealed(monkeypatch) -> tuple[bytes, bytes, str]:
    monkeypatch.setattr(egress_gateway, "_seal_key", lambda: b"k" * 32)
    return egress_gateway.seal_secret(_SECRET)


def test_rebind_requires_exact_current_canonical_approval(monkeypatch):
    ciphertext, nonce, fingerprint = _sealed(monkeypatch)
    historical_digest = money_shape.payload_digest({"connection_slug": _CONNECTION})
    conn = _ConnectionDb(
        approval_digest=historical_digest,
        ciphertext=ciphertext,
        nonce=nonce,
        fingerprint=fingerprint,
    )

    response = _client(monkeypatch, conn).post(
        "/v1/connections/rebind",
        headers=_headers(),
        json={"business": _BUSINESS, "connection_slug": _CONNECTION},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "connection_not_approved"
    assert conn.rebound is None


def test_rebind_reuses_intact_ciphertext_without_plaintext_egress(monkeypatch):
    ciphertext, nonce, fingerprint = _sealed(monkeypatch)
    conn = _ConnectionDb(
        approval_digest=_exact_approval_digest(),
        ciphertext=ciphertext,
        nonce=nonce,
        fingerprint=fingerprint,
    )

    response = _client(monkeypatch, conn).post(
        "/v1/connections/rebind",
        headers=_headers(),
        json={"business": _BUSINESS, "connection_slug": _CONNECTION},
    )

    assert response.status_code == 200
    assert response.json() == {
        "business": _BUSINESS,
        "connection_slug": _CONNECTION,
        "status": "active",
        "fingerprint": fingerprint,
    }
    assert _SECRET not in response.text
    assert conn.rebound == (egress_gateway.connection_scope_digest(_scope()), _CONNECTION_ID)


def test_rebind_refuses_tampered_ciphertext_without_activating(monkeypatch):
    ciphertext, nonce, fingerprint = _sealed(monkeypatch)
    conn = _ConnectionDb(
        approval_digest=_exact_approval_digest(),
        ciphertext=ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]),
        nonce=nonce,
        fingerprint=fingerprint,
    )

    response = _client(monkeypatch, conn).post(
        "/v1/connections/rebind",
        headers=_headers(),
        json={"business": _BUSINESS, "connection_slug": _CONNECTION},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {"error": "connection_unseal_failed"}
    assert _SECRET not in response.text
    assert conn.rebound is None


def test_rebind_refuses_fingerprint_mismatch_without_activating(monkeypatch):
    ciphertext, nonce, _fingerprint = _sealed(monkeypatch)
    conn = _ConnectionDb(
        approval_digest=_exact_approval_digest(),
        ciphertext=ciphertext,
        nonce=nonce,
        fingerprint="0" * 64,
    )

    response = _client(monkeypatch, conn).post(
        "/v1/connections/rebind",
        headers=_headers(),
        json={"business": _BUSINESS, "connection_slug": _CONNECTION},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {"error": "connection_fingerprint_mismatch"}
    assert _SECRET not in response.text
    assert conn.rebound is None


def test_rebind_client_uses_operator_authority_without_secret(monkeypatch):
    captured = {}

    def _remote(method, path, payload, **kwargs):
        captured.update(
            method=method,
            path=path,
            payload=payload,
            kwargs=kwargs,
        )
        return {"status": "active"}

    monkeypatch.setattr(safebox, "_remote_json", _remote)

    result = safebox.rebind_connection_secret(
        business=_BUSINESS,
        connection_slug=_CONNECTION,
    )

    assert result == {"status": "active"}
    assert captured == {
        "method": "POST",
        "path": "/v1/connections/rebind",
        "payload": {"business": _BUSINESS, "connection_slug": _CONNECTION},
        "kwargs": {"timeout": 30.0, "operator_authority": True},
    }
    assert "secret" not in captured["payload"]
