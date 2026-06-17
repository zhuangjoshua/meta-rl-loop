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
from plugins.takyon import app_usage, core, web_spend  # noqa: E402
from plugins.takyon.app_usage import list_usage_events, set_app_budget  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402

_AUX_MODEL = "claude-opus-4-8"  # a token-priced model for the summarizer-style event


def _provision_business(conn) -> str:
    uid, _created, _raw = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}")
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", uid),
    )
    return slug


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
    slug = _provision_business(pg_conn)
    set_app_budget(pg_conn, slug, hard_limit_microusd=10_000_000)  # $10 headroom
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
