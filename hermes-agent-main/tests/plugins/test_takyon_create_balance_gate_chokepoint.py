"""Company-creation balance gate is wired into the canonical create CHOKEPOINT — GOAL_RULES §3 gap
#2 (red-team proven: a zero-balance operator created a real businesses row via
``run_takyon_command(["create", ...])``).

The authoritative gate ``plugins.takyon.cli._operator_create_balance_preflight`` used to be called
ONLY by the ``takyon.dashboard.create`` gateway method, so the shell ``/create`` path and the bare
CLI ``create``/``init``/``build`` bypassed it and committed ``business.upsert`` with no balance check.
The fix moves the preflight into ``run_takyon_command``'s ``init``/``create``/``build`` branch — the
single chokepoint that the dashboard RPC, shell ``/create``, the ``--no-auto`` detached create, and
the bare CLI all funnel through — BEFORE the ``store.commit`` that writes the row.

These are seam tests: they patch ``_operator_create_balance_preflight`` and assert the create branch
calls it before any commit (fails CLOSED for a zero-balance operator, no row written) and that a
funded operator (preflight passes) still reaches the commit. No real money / no Postgres needed.
"""
from __future__ import annotations

import pytest

from plugins.takyon import cli as takyon_cli
from plugins.takyon.cli import InsufficientOperatorBalance


class _RecordingStore:
    """FakeStore for a FRESH slug: ``read`` returns an empty business so ``_business_exists`` is
    False (a real create), and ``commit`` records whether it was reached (i.e. a businesses row
    would have been written)."""

    def __init__(self, *args, **kwargs):
        self.commits: list[dict] = []

    def commit(self, *, scope, operations, **kwargs):
        self.commits.append({"scope": scope, "operations": operations})
        return {"results": [{"action": "business.upsert"}]}

    def read(self, *, scope, query, **kwargs):
        # _business_exists -> summary with no slug -> treated as not existing (fresh create).
        # After commit, the create branch also reads summary to confirm persistence.
        return {"business": {"slug": "acme", "mode": "live"}}


@pytest.fixture(autouse=True)
def _store_and_model(monkeypatch):
    monkeypatch.setattr(takyon_cli, "_read_model_config", lambda store: {})
    monkeypatch.setattr(takyon_cli, "_require_agent_model_config", lambda *a, **k: None)


def _install_store(monkeypatch) -> _RecordingStore:
    store = _RecordingStore()
    monkeypatch.setattr(takyon_cli, "TakyonStore", lambda *a, **k: store)
    # Fresh slug: force _business_exists False regardless of read() shape.
    monkeypatch.setattr(takyon_cli, "_business_exists", lambda *a, **k: False)
    return store


def test_zero_balance_create_blocks_before_business_row(monkeypatch):
    """A zero-balance operator: the preflight raises InsufficientOperatorBalance and the create
    branch must NOT reach store.commit — no businesses row is written. This is the exact red-team
    bypass (``run_takyon_command(["create", "--no-auto", slug, ...])``) now failing CLOSED."""
    store = _install_store(monkeypatch)
    called = {"preflight": 0}

    def _blocking_preflight(operator_user_id):
        called["preflight"] += 1
        raise InsufficientOperatorBalance(
            spendable_cents=0, allowance_remaining_cents=0, topup_balance_cents=0
        )

    monkeypatch.setattr(takyon_cli, "_operator_create_balance_preflight", _blocking_preflight)

    with pytest.raises(InsufficientOperatorBalance):
        takyon_cli.run_takyon_command(
            ["create", "--no-auto", "acme", "a real company"],
            model="",
            max_turns=7,
            operator_user_id="op-zero-balance",
        )

    assert called["preflight"] == 1, "create chokepoint must invoke the balance preflight"
    assert store.commits == [], "no business.upsert may be committed when the operator is unfunded"


def test_funded_create_passes_gate_and_commits(monkeypatch):
    """A funded operator: the preflight returns normally, so the create branch proceeds to
    store.commit and a businesses row is written. Proves the gate does not block funded operators."""
    store = _install_store(monkeypatch)
    called = {"preflight": 0}

    def _passing_preflight(operator_user_id):
        called["preflight"] += 1
        return None

    monkeypatch.setattr(takyon_cli, "_operator_create_balance_preflight", _passing_preflight)

    result = takyon_cli.run_takyon_command(
        ["create", "--no-auto", "acme", "a real company"],
        model="",
        max_turns=7,
        operator_user_id="op-funded",
    )

    assert called["preflight"] == 1
    assert any(
        any((op or {}).get("action") == "business.upsert" for op in c["operations"])
        for c in store.commits
    ), "funded operator must reach the business.upsert commit"
    assert isinstance(result, dict)


def test_preflight_runs_before_commit_for_bare_init(monkeypatch):
    """The same chokepoint covers the bare CLI ``init`` verb (no --no-auto, auto_default False), not
    just ``create`` — ordering check: preflight fires, and if it raises nothing commits."""
    store = _install_store(monkeypatch)

    order: list[str] = []

    def _blocking_preflight(operator_user_id):
        order.append("preflight")
        raise InsufficientOperatorBalance(
            spendable_cents=0, allowance_remaining_cents=0, topup_balance_cents=0
        )

    orig_commit = store.commit

    def _tracking_commit(*, scope, operations, **kwargs):
        order.append("commit")
        return orig_commit(scope=scope, operations=operations, **kwargs)

    store.commit = _tracking_commit  # type: ignore[method-assign]
    monkeypatch.setattr(takyon_cli, "_operator_create_balance_preflight", _blocking_preflight)

    with pytest.raises(InsufficientOperatorBalance):
        takyon_cli.run_takyon_command(
            ["init", "acme", "a real company"],
            model="",
            max_turns=7,
            operator_user_id="op-zero-balance",
        )

    assert order == ["preflight"], "preflight must run and block before any commit"
