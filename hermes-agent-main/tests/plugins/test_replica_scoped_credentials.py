"""Per-replica SCOPED, REVOCABLE credentials — the Stage 4b hardening bullet's runtime seams.

Three seams, one invariant each:

- ``runtime_app.assert_takyon_pg_role`` accepts a scoped replica login (``takyon_app_runtime__<node>``)
  for the app plane ONLY when it holds live inherited membership of the canonical role — name alone
  is never authority, membership alone is never authority (takyon_migration's non-inherit admin
  membership must keep failing), and every pre-existing rejection stays byte-identical.
- ``app_identity._is_app_runtime_user`` recognizes scoped members so identity/session writes keep
  routing through the app-runtime SECURITY DEFINER ports under a scoped login.
- ``safebox_app._require_internal_token`` validates a token SET: the shared token keeps working
  unchanged (non-split hosts), enrolled per-node tokens (sha256 digests on disk, values never
  stored) are accepted, and a revoked/unknown token is 401 — fail closed, no restart needed.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from plugins.takyon import app_identity
from plugins.takyon import runtime_app
from plugins.takyon import safebox_app
from plugins.takyon.runtime_app import DatabaseRoleMismatch, assert_takyon_pg_role, scoped_plane_role_name


_SCOPED = "takyon_app_runtime__takyon_dev_subuser_1"


class _ScopedRoleConn:
    """Answers the role probe AND the pg_has_role membership probe."""

    def __init__(self, *, session_user: str, current_user: str, member: bool):
        self.session_user = session_user
        self.current_user = current_user
        self.member = member
        self.membership_queries: list[tuple] = []

    def execute(self, sql, params=None):
        text = str(sql)
        if "pg_has_role" in text:
            self.membership_queries.append(tuple(params or ()))
            return _Result({"scoped_member": self.member})
        if "current_user" in text and "session_user" in text:
            return _Result({"session_user": self.session_user, "current_user": self.current_user})
        return _Result({"current_user": self.current_user})


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


# ── runtime_app.assert_takyon_pg_role ────────────────────────────────────────────────────────


def test_scoped_role_name_is_sanitized_and_bounded():
    assert scoped_plane_role_name("takyon_app_runtime", "takyon-dev-subuser-1") == _SCOPED
    with pytest.raises(ValueError):
        scoped_plane_role_name("takyon_app_runtime", "")
    with pytest.raises(ValueError):
        scoped_plane_role_name("takyon_app_runtime", "x" * 80)


def test_assert_role_accepts_scoped_member_for_app_plane():
    conn = _ScopedRoleConn(session_user=_SCOPED, current_user=_SCOPED, member=True)
    assert assert_takyon_pg_role(conn, "app") == (_SCOPED, _SCOPED)
    assert conn.membership_queries, "membership must be verified against the live catalog"
    assert (_SCOPED, "takyon_app_runtime") in conn.membership_queries


def test_assert_role_rejects_scoped_name_without_membership():
    """A role merely NAMED like a scoped login is refused — the name is not authority."""
    conn = _ScopedRoleConn(session_user=_SCOPED, current_user=_SCOPED, member=False)
    with pytest.raises(DatabaseRoleMismatch, match="app database role mismatch"):
        assert_takyon_pg_role(conn, "app")


def test_assert_role_rejects_member_without_scoped_name():
    """takyon_migration IS a member of takyon_app_runtime (admin option) — it must keep failing
    the app plane: membership alone is not authority either."""
    conn = _ScopedRoleConn(session_user="takyon_migration", current_user="takyon_migration", member=True)
    with pytest.raises(DatabaseRoleMismatch, match="app database role mismatch"):
        assert_takyon_pg_role(conn, "app")
    assert conn.membership_queries == [], "a non-scoped name must not even reach the catalog probe"


def test_assert_role_rejects_scoped_app_login_on_operator_plane():
    conn = _ScopedRoleConn(session_user=_SCOPED, current_user=_SCOPED, member=True)
    with pytest.raises(DatabaseRoleMismatch, match="operator database role mismatch"):
        assert_takyon_pg_role(conn, "operator")


def test_assert_role_rejects_demoted_scoped_session():
    """One scoped leg + one foreign leg is still a mismatch (both legs must be app-plane)."""
    conn = _ScopedRoleConn(session_user="takyon_runtime", current_user=_SCOPED, member=True)
    with pytest.raises(DatabaseRoleMismatch, match="app database role mismatch"):
        assert_takyon_pg_role(conn, "app")


# ── app_identity._is_app_runtime_user ────────────────────────────────────────────────────────


class _IdentityConn:
    def __init__(self, user: str, member: bool):
        self.user = user
        self.member = member

    def execute(self, sql, params=None):
        if "pg_has_role" in str(sql):
            return _Result((self.member,))
        return _Result((self.user,))


def test_app_identity_accepts_scoped_member():
    assert app_identity._is_app_runtime_user(_IdentityConn(_SCOPED, member=True)) is True


def test_app_identity_rejects_scoped_name_without_membership():
    assert app_identity._is_app_runtime_user(_IdentityConn(_SCOPED, member=False)) is False


def test_app_identity_canonical_roles_unchanged():
    assert app_identity._is_app_runtime_user(_IdentityConn("takyon_app_runtime", member=False)) is True
    assert app_identity._is_app_runtime_user(_IdentityConn("takyon_app", member=False)) is True
    assert app_identity._is_app_runtime_user(_IdentityConn("takyon_migration", member=True)) is False


# ── safebox_app._require_internal_token: shared token + per-node token set ──────────────────


from fastapi import HTTPException  # noqa: E402


def _write_tokens(tmp_path, nodes: dict[str, str]):
    path = tmp_path / "node_tokens.json"
    path.write_text(json.dumps({
        "version": 1,
        "nodes": {name: {"token_sha256": hashlib.sha256(value.encode()).hexdigest()}
                  for name, value in nodes.items()},
    }))
    return path


@pytest.fixture()
def token_env(tmp_path, monkeypatch):
    monkeypatch.delenv("TAKYON_SAFEBOX_ALLOW_TOKENLESS", raising=False)
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    path = _write_tokens(tmp_path, {"takyon-dev-subuser-1": "node-one-token"})
    monkeypatch.setenv("TAKYON_SAFEBOX_NODE_TOKENS_PATH", str(path))
    return path


def test_shared_token_still_accepted_with_node_file(token_env):
    assert safebox_app._require_internal_token("Bearer shared-token") is None


def test_enrolled_node_token_accepted(token_env):
    assert safebox_app._require_internal_token("Bearer node-one-token") is None


def test_unknown_token_is_401(token_env):
    with pytest.raises(HTTPException) as exc:
        safebox_app._require_internal_token("Bearer some-other-token")
    assert exc.value.status_code == 401


def test_revoked_node_token_is_401_without_restart(token_env, tmp_path):
    """Revocation = the digest leaves the file; the very next request is refused (mtime re-read,
    no process restart)."""
    assert safebox_app._require_internal_token("Bearer node-one-token") is None
    _write_tokens(tmp_path, {})  # prune the node — same path the provisioner rewrites
    with pytest.raises(HTTPException) as exc:
        safebox_app._require_internal_token("Bearer node-one-token")
    assert exc.value.status_code == 401


def test_node_tokens_alone_suffice_without_shared_token(tmp_path, monkeypatch):
    """A safebox host may run with ONLY enrolled node tokens; absence of the shared token does not
    mean 'not configured' when digests are enrolled."""
    monkeypatch.delenv("TAKYON_SAFEBOX_ALLOW_TOKENLESS", raising=False)
    monkeypatch.delenv("TAKYON_SAFEBOX_TOKEN", raising=False)
    path = _write_tokens(tmp_path, {"n1": "only-node-token"})
    monkeypatch.setenv("TAKYON_SAFEBOX_NODE_TOKENS_PATH", str(path))
    assert safebox_app._require_internal_token("Bearer only-node-token") is None
    with pytest.raises(HTTPException):
        safebox_app._require_internal_token("Bearer nope")


def test_unconfigured_still_fails_closed(tmp_path, monkeypatch):
    """No shared token + no node file = the exact pre-existing fail-closed behavior."""
    monkeypatch.delenv("TAKYON_SAFEBOX_ALLOW_TOKENLESS", raising=False)
    monkeypatch.delenv("TAKYON_SAFEBOX_TOKEN", raising=False)
    monkeypatch.setenv("TAKYON_SAFEBOX_NODE_TOKENS_PATH", str(tmp_path / "absent.json"))
    with pytest.raises(HTTPException) as exc:
        safebox_app._require_internal_token("Bearer anything")
    assert exc.value.status_code == 401
    assert exc.value.detail == "safebox token not configured"


def test_malformed_node_file_contributes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("TAKYON_SAFEBOX_ALLOW_TOKENLESS", raising=False)
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    path = tmp_path / "node_tokens.json"
    path.write_text('{"nodes": {"n1": {"token_sha256": "not-a-hex-digest"}}, junk')
    monkeypatch.setenv("TAKYON_SAFEBOX_NODE_TOKENS_PATH", str(path))
    assert safebox_app._require_internal_token("Bearer shared-token") is None
    with pytest.raises(HTTPException):
        safebox_app._require_internal_token("Bearer not-a-hex-digest")
