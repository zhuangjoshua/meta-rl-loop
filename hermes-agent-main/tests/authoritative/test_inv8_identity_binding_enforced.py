"""Authoritative money-safety suite — INVARIANT 8: Identity binding enforced.

GOAL_RULES.md §3, invariant 8:
    "Identity binding enforced — TAKYON_REQUIRE_OPERATOR_IDENTITY=enforce in any
     multi-tenant plane; an unbound session cannot reach business access."

What this invariant protects against:
    On a multi-tenant (dashboard) plane, a Takyon operator session that has LOST
    (or never carried) its user binding must NOT silently fall through to
    all-business access. With the plane flipped to `enforce`, an unbound session
    is refused at the access gate BEFORE any business read happens, and a
    session bound to operator A cannot reach a business owned by operator B.

Grounded symbols (all RE-CONFIRMED by opening
``plugins/takyon/core.py`` before writing this test):

    * ``plugins.takyon.core.operator_identity_mode``
        module-level fn; parses ``TAKYON_REQUIRE_OPERATOR_IDENTITY`` ->
        '' | 'warn' | 'enforce' (core.py:145-157).
    * ``plugins.takyon.core.TakyonStore`` (core.py:10943) with
      ``__init__(self, root=None, *, database_url=None, operator_user_id=None,
      system_plane="")`` (core.py:10946-10999).
        - On an identity-declaring plane (mode non-empty), the process-global
          ``TAKYON_OPERATOR_USER_ID`` env is IGNORED (core.py:10985); a principal
          can only arrive via the ``operator_user_id`` arg or session env.
    * ``TakyonStore._active_operator_user_id`` (core.py:11072) -> the bound id.
    * ``TakyonStore._enforce_operator_business_access(self, conn, business_slug)``
      (core.py:11075) — the access gate:
        - unbound + ``_system_plane`` set -> returns (runtime serving exempt),
        - unbound + mode 'enforce' -> raises ``TakyonError`` (FAIL CLOSED),
        - unbound + mode 'warn' -> records evidence, then allows,
        - unbound + mode '' -> historical allow,
        - bound + owner mismatch -> raises ``TakyonError`` (cross-tenant refusal).
    * ``plugins.takyon.core.TakyonError`` (core.py:1004) — subclass of RuntimeError.

Hermetic notes:
    The repo conftest scrubs ``TAKYON_REQUIRE_OPERATOR_IDENTITY`` is NOT in its
    behavioral-var list, so each test sets/clears it explicitly via monkeypatch.
    The credential filter and ``TAKYON_HOME`` redirect still apply. None of the
    no-credential tests below open a real DB: the enforce/unbound refusal fires
    before any ``conn.execute``, and the cross-tenant refusal is driven with a
    tiny in-test fake connection that mimics the single ``SELECT owner_user_id``
    the gate performs. One source-structure test reads core.py text. No network,
    no Postgres, no provider keys required.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from plugins.takyon.core import (
    TakyonError,
    TakyonStore,
    operator_identity_mode,
)

# The env var the operator MUST set to `enforce` on every multi-tenant (dashboard)
# plane in production. Documented here so the invariant's prod requirement is
# explicit and asserted, not just folklore.
REQUIRE_OPERATOR_IDENTITY_ENV = "TAKYON_REQUIRE_OPERATOR_IDENTITY"
PROD_ENFORCE_VALUE = "enforce"

CORE_PATH = Path(inspect.getfile(TakyonStore))


# ── Fakes (no DB, no network) ──────────────────────────────────────────────


class _FakeRow(dict):
    """Mimics the dict_row factory the store uses: indexable by column name."""


class _FakeConn:
    """Minimal stand-in for the single ``SELECT owner_user_id FROM businesses``
    the access gate runs once a principal is bound. Lets the bound/cross-tenant
    refusal path be exercised with zero credentials and zero Postgres.

    ``owner`` None models a non-existent business row; a string models the
    business's stored ``owner_user_id``.
    """

    def __init__(self, owner: str | None):
        self._owner = owner
        self.execute_calls = 0

    def execute(self, _sql, _params=()):
        self.execute_calls += 1
        return self

    def fetchone(self):
        if self._owner is None:
            return None
        return _FakeRow(owner_user_id=self._owner)


class _ExplodingConn:
    """A connection that fails LOUDLY if touched. Used to prove the enforce
    refusal fires BEFORE any business read — i.e. the gate never reaches the DB
    for an unbound session on an enforcing plane."""

    def execute(self, *_a, **_k):  # pragma: no cover - must never be called
        raise AssertionError(
            "access gate touched the DB for an unbound session under enforce; "
            "it must refuse before any business read"
        )


def _store(operator_user_id: str | None = None, *, system_plane: str = ""):
    """Construct a TakyonStore without opening the DB.

    ``root`` is left default (conftest redirects TAKYON_HOME to a tempdir, and
    __init__ only resolves the path; it does not connect). We pass the principal
    explicitly because, on an identity-declaring plane, the env-based
    TAKYON_OPERATOR_USER_ID source is intentionally ignored.
    """
    return TakyonStore(
        operator_user_id=operator_user_id,
        system_plane=system_plane,
    )


# ── Env -> mode contract (the prod requirement) ────────────────────────────


def test_enforce_env_value_selects_enforce_mode(monkeypatch):
    """The exact production value documented by the invariant must map to the
    fail-closed mode."""
    monkeypatch.setenv(REQUIRE_OPERATOR_IDENTITY_ENV, PROD_ENFORCE_VALUE)
    assert operator_identity_mode() == "enforce"


def test_enforce_aliases_map_to_enforce(monkeypatch):
    for raw in ("enforce", "ENFORCE", "Enforce", "1", "true", "yes", "on", " enforce "):
        monkeypatch.setenv(REQUIRE_OPERATOR_IDENTITY_ENV, raw)
        assert operator_identity_mode() == "enforce", raw


def test_unset_or_falsey_is_not_enforce(monkeypatch):
    """Unset / disabled values are NOT enforce — which is exactly why the
    invariant requires operators to set it explicitly in prod."""
    monkeypatch.delenv(REQUIRE_OPERATOR_IDENTITY_ENV, raising=False)
    assert operator_identity_mode() == ""
    for raw in ("", "0", "false", "off", "garbage"):
        monkeypatch.setenv(REQUIRE_OPERATOR_IDENTITY_ENV, raw)
        assert operator_identity_mode() != "enforce", raw


def test_warn_is_its_own_observe_mode(monkeypatch):
    """`warn` observes (records evidence) but does NOT fail closed — so it does
    not satisfy invariant 8's prod requirement on its own."""
    monkeypatch.setenv(REQUIRE_OPERATOR_IDENTITY_ENV, "warn")
    assert operator_identity_mode() == "warn"


# ── The core invariant: unbound session cannot reach business access ───────


def test_unbound_session_under_enforce_is_refused(monkeypatch):
    """INVARIANT 8 (primary): with the plane enforcing, a session that carries
    no operator principal is REFUSED business access — and the refusal happens
    before any business read (we pass an exploding conn to prove that)."""
    monkeypatch.setenv(REQUIRE_OPERATOR_IDENTITY_ENV, PROD_ENFORCE_VALUE)
    store = _store(operator_user_id=None)
    # Sanity: this really is an unbound principal on an enforcing plane.
    assert store._active_operator_user_id() == ""

    with pytest.raises(TakyonError) as excinfo:
        store._enforce_operator_business_access(_ExplodingConn(), "victim-biz")

    msg = str(excinfo.value).lower()
    assert "operator identity required" in msg
    assert "victim-biz" in str(excinfo.value)


def test_unbound_session_without_enforce_is_not_refused_by_this_gate(monkeypatch):
    """Negative control: the refusal is produced specifically by enforce mode,
    not by some unrelated always-on guard. With the plane UNSET, an unbound
    session is allowed through this gate (historical single-operator behavior).

    This pins the behavior to the env flag, so the enforce test above proves the
    flag is load-bearing rather than incidental."""
    monkeypatch.delenv(REQUIRE_OPERATOR_IDENTITY_ENV, raising=False)
    store = _store(operator_user_id=None)
    assert store._active_operator_user_id() == ""
    # No raise: unbound + unset mode returns without touching the DB.
    store._enforce_operator_business_access(_ExplodingConn(), "some-biz")


def test_unbound_warn_mode_allows_but_records(monkeypatch):
    """`warn` must NOT fail closed (it allows) — confirming only `enforce`
    satisfies invariant 8. We give it a real fake conn because warn records an
    evidence event; allowing is the point, the event is best-effort."""
    monkeypatch.setenv(REQUIRE_OPERATOR_IDENTITY_ENV, "warn")
    store = _store(operator_user_id=None)
    conn = _FakeConn(owner="ignored")
    # Should not raise. _warn_unbound_operator_access best-effort records and
    # swallows its own exceptions, so even if event recording errors, access is
    # allowed under warn — which is exactly why warn != enforce.
    store._enforce_operator_business_access(conn, "warned-biz")


# ── Cross-tenant: a bound-but-mismatched session cannot reach another owner ─


def test_bound_session_can_reach_its_own_business(monkeypatch):
    """A session bound to operator A reaches a business A owns — enforcement
    must not break legitimate same-owner access."""
    monkeypatch.setenv(REQUIRE_OPERATOR_IDENTITY_ENV, PROD_ENFORCE_VALUE)
    store = _store(operator_user_id="op-A")
    assert store._active_operator_user_id() == "op-A"
    conn = _FakeConn(owner="op-A")
    # No raise.
    store._enforce_operator_business_access(conn, "owned-biz")
    assert conn.execute_calls == 1  # the gate did resolve ownership server-side


def test_bound_session_cannot_reach_another_operators_business(monkeypatch):
    """INVARIANT 8 / cross-tenant: a session bound to operator A is REFUSED a
    business owned by operator B. Ownership is resolved server-side from the
    slug; the caller never supplies an owner id."""
    monkeypatch.setenv(REQUIRE_OPERATOR_IDENTITY_ENV, PROD_ENFORCE_VALUE)
    store = _store(operator_user_id="op-A")
    conn = _FakeConn(owner="op-B")
    with pytest.raises(TakyonError) as excinfo:
        store._enforce_operator_business_access(conn, "rivals-biz")
    assert "access denied" in str(excinfo.value).lower()
    assert "rivals-biz" in str(excinfo.value)


def test_process_global_operator_env_is_ignored_under_enforce(monkeypatch):
    """A multi-tenant plane (mode non-empty) must NOT let a process-wide
    ``TAKYON_OPERATOR_USER_ID`` satisfy a per-session principal. Otherwise one
    leaked process env value would bind every session to one operator and defeat
    the whole point of per-session identity."""
    monkeypatch.setenv(REQUIRE_OPERATOR_IDENTITY_ENV, PROD_ENFORCE_VALUE)
    monkeypatch.setenv("TAKYON_OPERATOR_USER_ID", "sneaky-global-op")
    # No explicit operator_user_id arg, no session env -> must remain unbound.
    store = TakyonStore(system_plane="")
    assert store._active_operator_user_id() == ""
    with pytest.raises(TakyonError):
        store._enforce_operator_business_access(_ExplodingConn(), "any-biz")


# ── System-plane exemption is narrow and code-only ─────────────────────────


def test_system_plane_is_exempt_from_missing_principal_gate(monkeypatch):
    """The ONLY documented exemption: a store explicitly constructed with
    ``system_plane`` set (trusted in-process runtime serving, e.g. product-host
    routing) is allowed to read businesses without a bound principal even under
    enforce. This is code-only — it cannot arrive via request params or env."""
    monkeypatch.setenv(REQUIRE_OPERATOR_IDENTITY_ENV, PROD_ENFORCE_VALUE)
    store = _store(operator_user_id=None, system_plane="web_server")
    assert store._active_operator_user_id() == ""
    # No raise: the system-plane marker exempts only the missing-principal gate
    # and returns before any DB read.
    store._enforce_operator_business_access(_ExplodingConn(), "served-biz")


def test_system_plane_cannot_be_set_from_environment(monkeypatch):
    """Defense in depth: there is no env/request path that flips a store into
    system-plane. A default store under enforce stays gated even if an attacker
    sets plausible-looking env vars."""
    monkeypatch.setenv(REQUIRE_OPERATOR_IDENTITY_ENV, PROD_ENFORCE_VALUE)
    monkeypatch.setenv("TAKYON_SYSTEM_PLANE", "1")
    monkeypatch.setenv("SYSTEM_PLANE", "web_server")
    store = TakyonStore()  # no system_plane arg
    with pytest.raises(TakyonError):
        store._enforce_operator_business_access(_ExplodingConn(), "any-biz")


# ── Source-structure pins (cheap regression tripwires) ─────────────────────


def test_enforce_branch_present_in_source():
    """Structural tripwire: the gate must contain an explicit enforce branch
    that raises before falling through to access. Guards against a future edit
    that drops the fail-closed branch while keeping the warn/observe one."""
    src = CORE_PATH.read_text()
    gate_src = inspect.getsource(TakyonStore._enforce_operator_business_access)
    assert 'mode == "enforce"' in gate_src
    assert "raise TakyonError" in gate_src
    assert "operator_identity_mode()" in gate_src
    # The env var name must remain the single source of truth for the policy.
    assert REQUIRE_OPERATOR_IDENTITY_ENV in src


def test_identity_declaring_plane_ignores_global_operator_env_in_source():
    """The per-session isolation property is implemented in __init__: on an
    identity-declaring plane the process-global env is zeroed out. Pin that so a
    refactor can't silently re-admit the global env as a principal source."""
    init_src = inspect.getsource(TakyonStore.__init__)
    assert "operator_identity_mode()" in init_src
    assert "TAKYON_OPERATOR_USER_ID" in init_src
