"""Web spend boundary — real-budget proof (Postgres).

Drives the Takyon BusinessBudgetSpendMeter through the public seam (reserve/settle/release_paid_call)
against a real business app_budget and asserts the app_usage_events rows. Complements the wiring
proof in test_takyon_web_spend_meter.py: this is where "reserve + settle", "reserve + release",
"two settled spends", and the fail-closed refusals are verified against actual DB state.

Skips unless psycopg is importable and TAKYON_TEST_PG_DSN is set (see tests/conftest.py).
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest

psycopg = pytest.importorskip("psycopg")

from agent import web_spend_meter  # noqa: E402
from agent.usage_pricing import CanonicalUsage, has_known_pricing  # noqa: E402
from plugins.takyon import app_usage, billing, core, web_spend  # noqa: E402
from plugins.takyon.app_usage import list_usage_events, set_app_budget  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402

_AUX_MODEL = "claude-opus-4-8"  # a token-priced model for the summarizer-style event


def _provision_business(conn, *, operator_allowance_cents: int = 10_000) -> tuple[str, str]:
    """Provision an operator + a business owned by them; return (slug, owner_user_id).

    The OPERATOR money rail (billing.py) is the ceiling the web-egress meter gates on. First-login
    provisioning already grants the production starter allowance, but we then SET the allowance to
    exactly ``operator_allowance_cents`` (grant_allowance is a set-and-reset, not an add) so each
    test controls the operator's authority deterministically: $100 funds normal egress, $0 models a
    fully-exhausted operator (no money authority → web egress must fail closed)."""
    uid, _created, _raw = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}")
    # grant_allowance SETS included to this value and resets used to 0 — overriding the starter
    # grant so the operator's authority is exactly what the test asks for (0 = exhausted).
    billing.grant_allowance(conn, uid, operator_allowance_cents, f"test-grant:{uid}")
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", uid),
    )
    return slug, uid


class _FakeStore:
    """Points the meter's reserve/settle/release at the test pg_conn without closing it."""

    def __init__(self, pgconn):
        self._pgconn = pgconn

    @contextmanager
    def _connect(self):
        yield core._PGConn(self._pgconn)  # wrapper only; we never call its __exit__ close path

    def _app_leaves(self):
        return {"usage": app_usage}

    @contextmanager
    def _leaf_conn(self, conn):
        from psycopg.rows import tuple_row

        raw = conn._pg
        prev = raw.row_factory
        raw.row_factory = tuple_row
        try:
            yield raw
        finally:
            raw.row_factory = prev


@pytest.fixture
def metered(pg_conn, monkeypatch):
    """A provisioned business with an active budget, the real budget meter installed, and the
    session scope set. Yields the business slug."""
    slug, _owner = _provision_business(pg_conn)  # funded operator ($100 allowance)
    set_app_budget(pg_conn, slug, hard_limit_microusd=10_000_000)  # $10 pool headroom
    monkeypatch.setattr(core, "_store", lambda: _FakeStore(pg_conn))
    monkeypatch.setenv("TAKYON_SESSION_BUSINESS_SLUG", slug)
    web_spend_meter.register_spend_meter(web_spend.BusinessBudgetSpendMeter())
    yield slug
    web_spend_meter.register_spend_meter(None)


def _events(pg_conn, slug):
    return list_usage_events(pg_conn, slug)


def test_paid_search_success_reserves_and_settles(metered, pg_conn):
    handle = web_spend_meter.reserve_paid_call(
        pricing_key=("tavily", "search"), provider="tavily", op="web_search", units=1, purpose="agent_web_search"
    )
    web_spend_meter.settle_paid_call(handle, units=1)
    events = _events(pg_conn, metered)
    assert len(events) == 1
    assert events[0].status == "completed"
    assert events[0].actual_cost_microusd == 8000  # $0.008


def test_paid_search_failure_reserves_and_releases(metered, pg_conn):
    handle = web_spend_meter.reserve_paid_call(
        pricing_key=("tavily", "search"), provider="tavily", op="web_search", units=1, purpose="agent_web_search"
    )
    web_spend_meter.release_paid_call(handle, error="provider_error")
    events = _events(pg_conn, metered)
    assert len(events) == 1
    assert events[0].status in ("failed", "released")
    assert events[0].actual_cost_microusd == 0  # nothing charged


def test_extract_success_settles_per_url(metered, pg_conn):
    handle = web_spend_meter.reserve_paid_call(
        pricing_key=("tavily", "extract"), provider="tavily", op="web_extract", units=3, purpose="agent_web_extract"
    )
    web_spend_meter.settle_paid_call(handle, units=3)
    events = _events(pg_conn, metered)
    assert len(events) == 1
    assert events[0].status == "completed"
    assert events[0].actual_cost_microusd == 24000  # 3 × $0.008


def test_extract_plus_summarizer_is_two_settled_spends(metered, pg_conn):
    if not has_known_pricing(_AUX_MODEL, provider="anthropic"):
        pytest.skip(f"{_AUX_MODEL} not priced in this build")
    # 1) the extract provider spend
    h1 = web_spend_meter.reserve_paid_call(
        pricing_key=("tavily", "extract"), provider="tavily", op="web_extract", units=1, purpose="agent_web_extract"
    )
    web_spend_meter.settle_paid_call(h1, units=1)
    # 2) the summarizer LLM spend (token-priced, provider-resolved) — invariant #8: this is NOT free
    usage = CanonicalUsage(input_tokens=2000, output_tokens=1000)
    summ_key = ("anthropic", _AUX_MODEL)
    expected = web_spend._price_microusd(summ_key, units=None, usage=usage)
    assert expected and expected > 0
    h2 = web_spend_meter.reserve_paid_call(
        pricing_key=summ_key, provider="aux", op="web_summarize", usage=usage, purpose="agent_web_summarize"
    )
    web_spend_meter.settle_paid_call(h2, usage=usage)

    completed = [e for e in _events(pg_conn, metered) if e.status == "completed"]
    assert len(completed) == 2
    assert sum(e.actual_cost_microusd for e in completed) == 8000 + expected


def test_over_budget_fails_closed(metered, pg_conn):
    set_app_budget(pg_conn, metered, hard_limit_microusd=0)  # active, zero headroom
    with pytest.raises(web_spend_meter.SpendBlocked):
        web_spend_meter.reserve_paid_call(
            pricing_key=("tavily", "search"), provider="tavily", op="web_search", units=1, purpose="agent_web_search"
        )
    assert _events(pg_conn, metered) == []  # nothing reserved


def test_unpriced_provider_fails_closed(metered, pg_conn):
    with pytest.raises(web_spend_meter.SpendBlocked):
        web_spend_meter.reserve_paid_call(
            pricing_key=("firecrawl", "search"), provider="firecrawl", op="web_search", units=1, purpose="agent_web_search"
        )
    assert _events(pg_conn, metered) == []


def test_no_pool_cap_unfunded_operator_fails_closed(pg_conn, monkeypatch):
    """THE HOLE invariant 9 opened on the web-egress meter. After invariant 9 the budget row opens
    with a NULL per-business pool cap; a CEO/agent web call carries no app_user_id, so the
    per-subuser gate inside reserve_usage cannot apply either. Without the operator-rail ceiling
    this would reserve unbounded ungated spend. With an UNFUNDED operator (0 billing authority) and
    NO explicit pool cap there is NO money authority — it MUST fail closed and write nothing."""
    slug, _owner = _provision_business(pg_conn, operator_allowance_cents=0)  # unfunded operator
    # Open the budget the way production does (no explicit pool cap → hard_limit stays NULL).
    app_usage.ensure_app_budget(pg_conn, slug)
    assert app_usage.get_app_budget(pg_conn, slug).hard_limit_microusd is None
    monkeypatch.setattr(core, "_store", lambda: _FakeStore(pg_conn))
    monkeypatch.setenv("TAKYON_SESSION_BUSINESS_SLUG", slug)
    web_spend_meter.register_spend_meter(web_spend.BusinessBudgetSpendMeter())
    try:
        with pytest.raises(web_spend_meter.SpendBlocked):
            web_spend_meter.reserve_paid_call(
                pricing_key=("tavily", "search"), provider="tavily", op="web_search",
                units=1, purpose="agent_web_search",
            )
        assert _events(pg_conn, slug) == []  # nothing reserved — no ungated spend leaked
    finally:
        web_spend_meter.register_spend_meter(None)


def test_no_pool_cap_funded_operator_is_allowed(pg_conn, monkeypatch):
    """The funded counterpart: a business whose OWNER has real operator billing authority HAS a
    money source, so paid web egress reserves and settles normally even with NO explicit
    per-business pool cap. Proves the operator-rail gate refuses only the unfunded case, not all
    NULL-cap businesses, and that the ceiling is the operator rail (billing.py), not a
    product-subuser subscription (this business has zero subscribers)."""
    slug, _owner = _provision_business(pg_conn)  # funded operator ($100 allowance)
    app_usage.ensure_app_budget(pg_conn, slug)
    assert app_usage.get_app_budget(pg_conn, slug).hard_limit_microusd is None
    monkeypatch.setattr(core, "_store", lambda: _FakeStore(pg_conn))
    monkeypatch.setenv("TAKYON_SESSION_BUSINESS_SLUG", slug)
    web_spend_meter.register_spend_meter(web_spend.BusinessBudgetSpendMeter())
    try:
        handle = web_spend_meter.reserve_paid_call(
            pricing_key=("tavily", "search"), provider="tavily", op="web_search",
            units=1, purpose="agent_web_search",
        )
        web_spend_meter.settle_paid_call(handle, units=1)
        events = _events(pg_conn, slug)
        assert len(events) == 1
        assert events[0].status == "completed"
        assert events[0].actual_cost_microusd == 8000
    finally:
        web_spend_meter.register_spend_meter(None)


def test_cost_over_operator_authority_fails_closed(pg_conn, monkeypatch):
    """The operator-rail ceiling actually BINDS: an operator funded with authority BELOW the
    per-call cost cannot do paid web egress even though the per-business pool cap is NULL. This is
    the non-null ceiling that replaces the invariant-9-removed pool cap.

    2 cents of operator authority == 20_000 microUSD; a 3-URL tavily extract costs 3 × $0.008 ==
    24_000 microUSD > the ceiling, so it must fail closed and write nothing."""
    slug, _owner = _provision_business(pg_conn, operator_allowance_cents=2)  # $0.02 == 20_000 µusd
    app_usage.ensure_app_budget(pg_conn, slug)
    assert app_usage.get_app_budget(pg_conn, slug).hard_limit_microusd is None
    monkeypatch.setattr(core, "_store", lambda: _FakeStore(pg_conn))
    monkeypatch.setenv("TAKYON_SESSION_BUSINESS_SLUG", slug)
    web_spend_meter.register_spend_meter(web_spend.BusinessBudgetSpendMeter())
    try:
        with pytest.raises(web_spend_meter.SpendBlocked):
            web_spend_meter.reserve_paid_call(
                pricing_key=("tavily", "extract"), provider="tavily", op="web_extract",
                units=3, purpose="agent_web_extract",  # 3 × 8000 = 24_000 µusd > 20_000 ceiling
            )
        assert _events(pg_conn, slug) == []  # nothing reserved — ceiling held
    finally:
        web_spend_meter.register_spend_meter(None)


def test_no_business_scope_is_not_metered(metered, pg_conn, monkeypatch):
    # Global / operator scope (no business): paid web egress proceeds UNMETERED — there is no
    # business budget to charge. The fail-closed cases are over-budget / unpriced / no-meter-in-a-
    # business-session, not "no business scope at all".
    monkeypatch.delenv("TAKYON_SESSION_BUSINESS_SLUG", raising=False)
    handle = web_spend_meter.reserve_paid_call(
        pricing_key=("tavily", "search"), provider="tavily", op="web_search", units=1, purpose="agent_web_search"
    )
    web_spend_meter.settle_paid_call(handle, units=1)  # no-op (nothing was reserved)
    assert _events(pg_conn, metered) == []  # no budget event created outside a business scope


# ── CUMULATIVE OPERATOR-AUTHORITY ENFORCEMENT (the red-team hole) ─────────────────────
#
# The bug: the old meter READ the operator ceiling but never DEBITED it, so a hold never
# decremented the authority. Two sequential reserves each under the per-call ceiling both held —
# 120%+ of the operator's whole authority could be outstanding at once, driving unbounded real
# Tavily spend in a CEO loop. These tests pin the fix: the web meter now takes a REAL billing.reserve
# hold against billing_accounts, so outstanding holds + settled spend cannot collectively exceed the
# operator ceiling. Each releases/settles its holds so no stale reserved row leaks.


def _operator_for_business(pg_conn, slug):
    return pg_conn.execute(
        "select owner_user_id from businesses where slug = %s", (slug,)
    ).fetchone()[0]


def _billing_remaining_cents(pg_conn, owner) -> int:
    """Spendable operator authority = allowance remaining, the only bucket billing.reserve draws
    from and the web meter's ceiling (the topup bucket was removed from the operator money rail)."""
    bal = billing.get_billing_balances(pg_conn, owner)
    return max(0, int(bal.allowance_remaining_cents))


def _funded_metered_business(pg_conn, monkeypatch, *, operator_allowance_cents):
    """A business whose operator authority is set EXACTLY (so the test controls the ceiling), with
    NO explicit per-business pool cap (hard_limit stays NULL) — so the ONLY money gate is the
    operator billing rail. Returns the slug; caller is responsible for clearing the meter."""
    slug, owner = _provision_business(pg_conn, operator_allowance_cents=operator_allowance_cents)
    app_usage.ensure_app_budget(pg_conn, slug)
    assert app_usage.get_app_budget(pg_conn, slug).hard_limit_microusd is None
    monkeypatch.setattr(core, "_store", lambda: _FakeStore(pg_conn))
    monkeypatch.setenv("TAKYON_SESSION_BUSINESS_SLUG", slug)
    web_spend_meter.register_spend_meter(web_spend.BusinessBudgetSpendMeter())
    return slug, owner


def test_two_sequential_reserves_second_fails_closed(pg_conn, monkeypatch):
    """The headline cumulative-enforcement proof. Operator authority is funded so that a SINGLE
    paid call (3-URL tavily extract = 24_000 µusd = 3 cents) fits, but TWO do not: authority is set
    to 5 cents (50_000 µusd), and 2 × 24_000 = 48_000 µusd reserves 2 × ceil(24_000/10_000) = 2 × 3
    = 6 cents > 5. The FIRST reserve holds; the SECOND must fail closed (the hole let BOTH hold)."""
    slug, owner = _funded_metered_business(pg_conn, monkeypatch, operator_allowance_cents=5)
    try:
        start = _billing_remaining_cents(pg_conn, owner)
        assert start == 5

        h1 = web_spend_meter.reserve_paid_call(
            pricing_key=("tavily", "extract"), provider="tavily", op="web_extract",
            units=3, purpose="agent_web_extract",
        )
        assert h1 is not None
        # The first hold DEBITED the operator ceiling (this is what the bug failed to do).
        after_first = _billing_remaining_cents(pg_conn, owner)
        assert after_first == start - 3, "first reserve must decrement operator remaining"

        # The SECOND reserve sees the decremented remaining (2 cents) — 3 cents needed > 2 → closed.
        with pytest.raises(web_spend_meter.SpendBlocked):
            web_spend_meter.reserve_paid_call(
                pricing_key=("tavily", "extract"), provider="tavily", op="web_extract",
                units=3, purpose="agent_web_extract",
            )
        # Only ONE reserved event exists — the second never held (no 120%-of-authority overhang).
        reserved = [e for e in _events(pg_conn, slug) if e.status == "reserved"]
        assert len(reserved) == 1
        assert after_first == _billing_remaining_cents(pg_conn, owner), "blocked call held nothing"
    finally:
        # Release every test hold — leave 0 stale reserved rows and full authority restored.
        web_spend_meter.release_paid_call(h1, error="test_cleanup")
        web_spend_meter.register_spend_meter(None)
    assert _billing_remaining_cents(pg_conn, owner) == 5  # hold returned on release
    assert [e for e in _events(pg_conn, slug) if e.status == "reserved"] == []


def test_settle_decrements_operator_remaining(pg_conn, monkeypatch):
    """A settled web call permanently consumes operator authority — the ceiling drops and stays
    dropped (the bug left it unchanged: 39,480,000 → 39,480,000)."""
    slug, owner = _funded_metered_business(pg_conn, monkeypatch, operator_allowance_cents=100)
    try:
        start = _billing_remaining_cents(pg_conn, owner)  # 100 cents
        handle = web_spend_meter.reserve_paid_call(
            pricing_key=("tavily", "search"), provider="tavily", op="web_search",
            units=1, purpose="agent_web_search",  # $0.008 = 8_000 µusd → ceil = 1 cent hold
        )
        assert _billing_remaining_cents(pg_conn, owner) == start - 1  # held
        web_spend_meter.settle_paid_call(handle, units=1)
        # After settle the spend is permanent: remaining stays decremented (the bug's hole was here).
        assert _billing_remaining_cents(pg_conn, owner) == start - 1
        completed = [e for e in _events(pg_conn, slug) if e.status == "completed"]
        assert len(completed) == 1
        assert completed[0].actual_cost_microusd == 8000
        # No stale reserved row left behind.
        assert [e for e in _events(pg_conn, slug) if e.status == "reserved"] == []
    finally:
        web_spend_meter.register_spend_meter(None)


def test_release_returns_operator_hold(pg_conn, monkeypatch):
    """A released (failed) web call returns the WHOLE hold to the operator authority — remaining is
    restored to its pre-reserve value and no spend is recorded."""
    slug, owner = _funded_metered_business(pg_conn, monkeypatch, operator_allowance_cents=100)
    try:
        start = _billing_remaining_cents(pg_conn, owner)
        handle = web_spend_meter.reserve_paid_call(
            pricing_key=("tavily", "extract"), provider="tavily", op="web_extract",
            units=3, purpose="agent_web_extract",  # 24_000 µusd → ceil = 3 cents hold
        )
        assert _billing_remaining_cents(pg_conn, owner) == start - 3  # held
        web_spend_meter.release_paid_call(handle, error="provider_error")
        assert _billing_remaining_cents(pg_conn, owner) == start  # hold fully returned
        events = _events(pg_conn, slug)
        assert all(e.status in ("failed", "released") for e in events)
        assert all(e.actual_cost_microusd == 0 for e in events)
    finally:
        web_spend_meter.register_spend_meter(None)


def test_double_reserve_same_key_holds_once(pg_conn, monkeypatch):
    """Idempotency preserved on the billing rail: replaying the SAME reservation key against
    billing.reserve returns the same split without holding twice (the meter generates a fresh key
    per public reserve, but the underlying billing op must be replay-safe). Drive it directly on the
    meter's billing hold to prove the key-level invariant."""
    slug, owner = _funded_metered_business(pg_conn, monkeypatch, operator_allowance_cents=100)
    try:
        start = _billing_remaining_cents(pg_conn, owner)
        key = f"agent:web_search:{uuid.uuid4().hex}"
        r1 = billing.reserve(pg_conn, owner, 3, key, business_slug=slug)
        r2 = billing.reserve(pg_conn, owner, 3, key, business_slug=slug)  # replay
        assert r1.total_cents == r2.total_cents == 3
        assert _billing_remaining_cents(pg_conn, owner) == start - 3  # held ONCE, not twice
        billing.release_reservation(pg_conn, key)
        billing.release_reservation(pg_conn, key)  # replay is a no-op (first finalizer wins)
        assert _billing_remaining_cents(pg_conn, owner) == start
    finally:
        web_spend_meter.register_spend_meter(None)
