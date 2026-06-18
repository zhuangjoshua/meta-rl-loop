#!/usr/bin/env python3
"""Re-verification grouped E2E (GOAL_RULES §3 inv1/inv9 + the two holes commit 79ea9149 closed).

Drives the TWO money paths the task names, through their REAL tool entrypoints (NOT the meter
seam directly, NOT unit mocks), against the local Postgres rig:

(a) CEO/agent web egress: the REAL ``tools.web_tools.web_search_tool`` / ``web_extract_tool`` —
    the exact functions the agent calls — inside a business scope whose OWNER has NO operator
    billing authority (unfunded) and whose app_budget opens with a NULL per-business pool cap
    (invariant 9). A paid provider (tavily-classified) is registered, but its ``search``/``extract``
    is a tripwire that records whether it was ever invoked. Expectation: the tool REFUSES
    (``success: False``) BEFORE any provider call (tripwire untouched) and writes NO ledger event —
    i.e. the operator-rail ceiling in web_spend.py binds, no ungated spend leaks.

(b) Product usage self-report: the REAL ``TakyonStore.commit()`` durable-write entrypoint with an
    ``app.usage.record`` op (a CUSTOMER_REACHABLE_APP_WRITES action) carrying a NULL sub-user and a
    POSITIVE cost. Expectation: REFUSED ``subscription_required`` (a null-subuser positive-cost
    record has no entitlement to gate on and, post-inv9, no pool cap), ledger untouched. A
    zero-cost null-subuser record is a pure audit event and stays allowed (control).

Run:  source harness/.pg_env ; python tests/mvp_goal/grouped_e2e_ungated_paths_reverify.py
Prints a STRICT JSON verdict line prefixed ``RESULT_JSON: ``.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
import uuid
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import asyncio

import psycopg
from psycopg.conninfo import make_conninfo

from agent import web_spend_meter
from agent.web_search_provider import WebSearchProvider
from agent import web_search_registry
from plugins.takyon import app_usage, billing, core, web_spend
from plugins.takyon.control_plane import ensure_platform_owner, provision_user_on_first_login
from plugins.takyon.db.runner import run_migrations

import tools.web_tools as web_tools

ADMIN_DSN = os.environ["TAKYON_TEST_PG_DSN"]

evidence: list[str] = []


def log(msg: str) -> None:
    evidence.append(msg)
    print(msg, flush=True)


def make_db() -> str:
    dbname = f"reverify_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f'create database "{dbname}"')
    dsn = make_conninfo(ADMIN_DSN, dbname=dbname)
    with psycopg.connect(dsn, autocommit=True) as conn:
        run_migrations(conn)
    return dsn


def drop_db(dsn: str) -> None:
    name = psycopg.conninfo.conninfo_to_dict(dsn)["dbname"]
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f'drop database if exists "{name}" with (force)')


def provision_business(conn, *, operator_allowance_cents: int) -> tuple[str, str]:
    """Operator + an owned business; SET the operator allowance to exactly the given value
    (grant_allowance is set-and-reset). 0 == fully-exhausted operator (no money authority)."""
    ensure_platform_owner(conn)
    uid, _c, _r = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}")
    billing.grant_allowance(conn, uid, operator_allowance_cents, f"reverify-grant:{uid}")
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", uid),
    )
    return slug, uid


# ── a FakeStore that points the meter at the live test connection (mirrors the repo PG test) ──
class _FakeStore:
    def __init__(self, pgconn):
        self._pgconn = pgconn

    @contextmanager
    def _connect(self):
        yield core._PGConn(self._pgconn)

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


# ── a paid-classified provider whose search/extract is a TRIPWIRE (proves no egress on refusal) ──
class _TripwireTavily(WebSearchProvider):
    """Named 'tavily' so provider_billing classifies it ('paid','tavily'). Its search/extract
    record whether they were ever reached; if the spend gate works they MUST stay False."""

    def __init__(self):
        self.search_called = False
        self.extract_called = False

    @property
    def name(self) -> str:
        return "tavily"

    def is_available(self) -> bool:
        return True

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def search(self, query, limit=5):
        self.search_called = True
        return {"success": True, "data": {"web": [{"title": "x", "url": "http://x", "description": "", "position": 1}]}}

    def extract(self, urls, format=None):
        self.extract_called = True
        return [{"url": u, "content": "x", "success": True} for u in urls]


results = {
    "web_search_refused_no_egress": False,
    "web_extract_refused_no_egress": False,
    "app_usage_record_null_subuser_positive_refused": False,
    "app_usage_record_null_subuser_zero_allowed": False,
}


# ─────────────────────────── (a) REAL web tool entrypoint ───────────────────────────
def run_web_tool_group():
    dsn = make_db()
    conn = psycopg.connect(dsn, autocommit=True)
    tw = _TripwireTavily()
    prev_meter = web_spend_meter.get_spend_meter()
    prev_slug = os.environ.get("TAKYON_SESSION_BUSINESS_SLUG")
    prev_store = core._store
    try:
        # UNFUNDED operator (0 cents) + a budget opened the production way: NO explicit pool cap,
        # so hard_limit stays NULL (invariant 9). After inv9, the ONLY ceiling on CEO/agent web
        # egress is the operator billing rail — which here is 0 -> must fail closed.
        slug, _owner = provision_business(conn, operator_allowance_cents=0)
        app_usage.ensure_app_budget(conn, slug)
        b = app_usage.get_app_budget(conn, slug)
        assert b.hard_limit_microusd is None, b.hard_limit_microusd
        log(f"[a] business {slug}: app_budget status={b.status!r} hard_limit_microusd=None (no pool cap), operator allowance=0")

        # Wire the REAL seam: register the tripwire provider as the active backend, force config to
        # select it, install the real BusinessBudgetSpendMeter, set the business session scope, and
        # point the meter's store at this live connection.
        web_search_registry.register_provider(tw)
        os.environ["TAKYON_WEB_BACKEND"] = "tavily"  # belt; selection also falls back to the sole paid provider
        core._store = lambda: _FakeStore(conn)
        os.environ["TAKYON_SESSION_BUSINESS_SLUG"] = slug
        web_spend_meter.register_spend_meter(web_spend.BusinessBudgetSpendMeter())

        # Pin config-driven selection to our provider via the registry's config reader, regardless
        # of any user config on disk: monkeypatch the config key reader to return 'tavily'.
        orig_read = web_search_registry._read_config_key
        web_search_registry._read_config_key = lambda section, key: "tavily" if section == "web" else orig_read(section, key)
        try:
            # ----- web_search_tool (REAL agent entrypoint) -----
            raw_out = web_tools.web_search_tool("anything", limit=3)
            out = json.loads(raw_out)
            ledger = app_usage.list_usage_events(conn, slug)
            log(f"[a1] web_search_tool -> success={out.get('success')!r} error={str(out.get('error'))[:90]!r} "
                f"provider.search_called={tw.search_called} ledger_events={len(ledger)}")
            results["web_search_refused_no_egress"] = (
                out.get("success") is False
                and tw.search_called is False        # NO egress
                and "operator budget authority exhausted" in str(out.get("error", ""))
                and len(ledger) == 0                  # nothing reserved
            )

            # ----- web_extract_tool (REAL agent entrypoint, async) -----
            raw_ex = asyncio.run(web_tools.web_extract_tool(["http://example.com/a", "http://example.com/b"], use_llm_processing=False))
            ex = json.loads(raw_ex)
            ledger2 = app_usage.list_usage_events(conn, slug)
            log(f"[a2] web_extract_tool -> success={ex.get('success')!r} error={str(ex.get('error'))[:90]!r} "
                f"provider.extract_called={tw.extract_called} ledger_events={len(ledger2)}")
            results["web_extract_refused_no_egress"] = (
                ex.get("success") is False
                and tw.extract_called is False
                and "operator budget authority exhausted" in str(ex.get("error", ""))
                and len(ledger2) == 0
            )
        finally:
            web_search_registry._read_config_key = orig_read
    finally:
        conn.close()
        # restore globals
        web_spend_meter.register_spend_meter(prev_meter)
        core._store = prev_store
        try:
            web_search_registry._reset_for_tests()
        except Exception:
            pass
        if prev_slug is None:
            os.environ.pop("TAKYON_SESSION_BUSINESS_SLUG", None)
        else:
            os.environ["TAKYON_SESSION_BUSINESS_SLUG"] = prev_slug
        os.environ.pop("TAKYON_WEB_BACKEND", None)
        drop_db(dsn)


# ─────────────────────── (b) REAL TakyonStore.commit -> app.usage.record ───────────────────────
def run_app_usage_record_group():
    dsn = make_db()
    home = REPO / ".reverify_home" / uuid.uuid4().hex[:8]
    home.mkdir(parents=True, exist_ok=True)
    try:
        slug = f"biz-{uuid.uuid4().hex[:8]}"
        with psycopg.connect(dsn, autocommit=True) as seed:
            ensure_platform_owner(seed)
            uid, _c, _r = provision_user_on_first_login(seed, f"auth0|{uuid.uuid4().hex}")
            seed.execute(
                "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
                (slug, "Acme", uid),
            )

        store = core.TakyonStore(home, database_url=dsn)

        # B1: positive-cost record with a NULL sub-user, via the REAL customer-reachable commit.
        refused = False
        refusal_msg = ""
        try:
            store.commit(
                scope=f"business:{slug}",
                operations=[{
                    "action": "app.usage.record",
                    "business": slug,
                    "app_user_id": None,           # null sub-user — no entitlement to gate on
                    "actual_cost_microusd": 5_000,  # POSITIVE -> would persist ungated completed spend
                    "purpose": "product_usage",
                    "route": "app",
                }],
                idempotency_key=f"rec-pos-{uuid.uuid4().hex}",
                principal={"kind": "session"},  # customer-reachable principal
            )
        except Exception as exc:
            refused = True
            refusal_msg = f"{type(exc).__name__}: {exc}"

        with psycopg.connect(dsn, autocommit=True) as c2:
            ev = app_usage.list_usage_events(c2, slug)
        log(f"[b1] app.usage.record null-subuser POSITIVE cost -> refused={refused} msg={refusal_msg[:110]!r} ledger_events={len(ev)}")
        results["app_usage_record_null_subuser_positive_refused"] = (
            refused and "subscription_required" in refusal_msg and len(ev) == 0
        )

        # B2 (control): a ZERO-cost null-subuser record is a pure audit event -> NOT money-gated.
        # We assert this at the MONEY-GATE layer the core handler actually invokes
        # (app_usage.record_completed_usage with null subuser + zero cost), NOT through commit():
        # commit()'s post-apply workspace-revision step is host-config-gated on this rig (unrelated
        # to money) and the existing grouped E2E avoids it for the same reason. The money gate
        # writing a zero-cost row is the behavior under test, and it precedes that workspace step.
        zero_ok = False
        try:
            with psycopg.connect(dsn, autocommit=True) as c3:
                app_usage.ensure_app_budget(c3, slug)
                event = app_usage.record_completed_usage(
                    c3,
                    slug,
                    actual_cost_microusd=0,            # zero-cost audit event
                    reservation_key=f"audit-{uuid.uuid4().hex}",
                    app_user_id=None,                  # null sub-user
                    user_monthly_limit_microusd=None,
                    purpose="audit",
                    route="app",
                )
                ev2 = app_usage.list_usage_events(c3, slug)
            zero_ok = (
                event.status == "completed"
                and event.actual_cost_microusd == 0
                and event.app_user_id is None
                and len(ev2) == 1
            )
            log(f"[b2] record_completed_usage null-subuser ZERO cost -> status={event.status!r} "
                f"actual={event.actual_cost_microusd} app_user_id={event.app_user_id!r} ledger_events={len(ev2)}")
        except Exception as exc:
            log(f"[b2] zero-cost record unexpectedly refused: {type(exc).__name__}: {exc}")
        results["app_usage_record_null_subuser_zero_allowed"] = zero_ok
    finally:
        import shutil
        shutil.rmtree(home, ignore_errors=True)
        drop_db(dsn)


def main():
    try:
        run_web_tool_group()
    except Exception:
        log("[a] FATAL:\n" + traceback.format_exc())
    try:
        run_app_usage_record_group()
    except Exception:
        log("[b] FATAL:\n" + traceback.format_exc())

    solid = all([
        results["web_search_refused_no_egress"],
        results["web_extract_refused_no_egress"],
        results["app_usage_record_null_subuser_positive_refused"],
        results["app_usage_record_null_subuser_zero_allowed"],
    ])
    verdict = {
        "check": "reverify-grouped-e2e",
        **results,
        "verdict": "SOLID" if solid else "HOLES",
        "evidence": " | ".join(evidence),
    }
    print("RESULT_JSON: " + json.dumps(verdict))


if __name__ == "__main__":
    main()
