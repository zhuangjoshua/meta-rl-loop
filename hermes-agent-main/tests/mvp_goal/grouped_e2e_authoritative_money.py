#!/usr/bin/env python3
"""Grouped E2E (GOAL_RULES §2 + §3 invariant 9 + gap #4) — drives the REAL money-rail handlers
against the local Postgres rig, NOT unit mocks.

Two real entrypoints, one throwaway DB each:

A. Product-AI spend via the REAL HTTP gateway mount (`build_runtime_app` -> POST
   /internal/ai-gateway/messages), the exact surface a generated app uses. Provider HTTPS call is
   stubbed through the `get_provider_caller` seam (no key, no network); auth/budget/ledger are the
   real engine on real Postgres.
     (1) sub-user with NO active subscription -> MUST be refused 402 subscription_required, NO free
         budget, ledger untouched.
     (2) sub-user WITH a paid subscription (included_ai_budget_microusd = N) -> per-user budget == N;
         spend allowed cumulatively up to N, then the next call refused 402
         app_user_budget_exceeded; per-business pool imposes NO separate $5 cap (default budget opens
         with hard_limit NULL).
     (3) exact-cost settle (no markup): settled actual == provider-reported token cost, no scaling.

B. Action reserve via the REAL `app_actions._reserve_usage` handler through a REAL
   `TakyonStore(database_url=...)` (real `_connect` -> `_PGConn`, real `_leaf_conn`, real
   `_app_leaves`, real `app_usage.reserve_usage`). This is the §3 gap #4 path.
     (4) an unentitled `service`/null-subuser action reserve does NOT fall through to a per-business
         pool — it is REFUSED (`subscription_required`), nothing reserved.

Run:  source harness/.pg_env ; python tests/mvp_goal/grouped_e2e_authoritative_money.py
Exits 0 and prints a STRICT JSON verdict line prefixed `RESULT_JSON: `.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import psycopg
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

from plugins.takyon import app_actions, app_entitlements, app_identity, app_usage
from plugins.takyon.ai_gateway import get_provider_caller, _user_monthly_budget_microusd
from plugins.takyon.app_gateway_keys import mint_gateway_key
from plugins.takyon.app_usage import list_usage_events, get_usage_summary, get_app_budget
from plugins.takyon.control_plane import ensure_platform_owner, provision_user_on_first_login
from plugins.takyon.runtime_app import build_runtime_app
from plugins.takyon import core as takyon_core
from plugins.takyon.db.runner import run_migrations

ADMIN_DSN = os.environ["TAKYON_TEST_PG_DSN"]
GENERATE_BODY = {"messages": [{"role": "user", "content": "Hello gateway"}], "max_tokens": 32}

evidence: list[str] = []


def log(msg: str) -> None:
    evidence.append(msg)
    print(msg, flush=True)


def make_db() -> str:
    """Create a throwaway DB, apply real migrations; return its DSN."""
    dbname = f"mvpe2e_{uuid.uuid4().hex[:10]}"
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


def provision_business(conn) -> tuple[str, str]:
    """Real platform owner + a business they own + one gateway key. Mirrors the repo PG tests."""
    ensure_platform_owner(conn)  # idempotent operator bootstrap, like the serving flip
    uid, _created, _raw = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}")
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", uid),
    )
    raw, _record = mint_gateway_key(conn, slug)
    return slug, raw


def seed_paid_session_user(conn, slug, *, included_ai_budget_microusd, tier="paid", email=None):
    """A magic-link sub-user with an ACTIVE paid entitlement on a plan whose
    included_ai_budget_microusd == the per-user AI budget (the `y` term). Exact repo pattern."""
    email = email or f"cust-{uuid.uuid4().hex[:6]}@example.com"
    plan_key = f"{tier}-plan-{uuid.uuid4().hex[:4]}"
    price_cents = max(1, (int(included_ai_budget_microusd) + 9_999) // 10_000)
    app_entitlements.upsert_plan_policy(
        conn, slug, plan_key, tier=tier, price_cents=price_cents,
        included_ai_budget_microusd=included_ai_budget_microusd,
    )
    _link, raw_magic = app_identity.create_magic_link(conn, slug, email)
    session_user, session_token = app_identity.verify_magic_link(conn, slug, raw_magic)
    app_entitlements.grant_entitlement(
        conn, slug, app_user_id=session_user.app_user_id, tier=tier, status="active",
        source="stripe", plan_key=plan_key, stripe_subscription_id=f"sub_{plan_key}",
    )
    user = app_identity.get_app_user(conn, slug, app_user_id=session_user.app_user_id)
    return user, session_token, plan_key


def seed_unentitled_session_user(conn, slug, *, email=None):
    """A magic-link sub-user with NO entitlement / no plan — the freeloader."""
    email = email or f"noplan-{uuid.uuid4().hex[:6]}@example.com"
    _link, raw_magic = app_identity.create_magic_link(conn, slug, email)
    session_user, session_token = app_identity.verify_magic_link(conn, slug, raw_magic)
    user = app_identity.get_app_user(conn, slug, app_user_id=session_user.app_user_id)
    return user, session_token


def canned_caller():
    """Deterministic Anthropic-shaped response: 100 in / 20 out tokens. Never touches a key/network."""
    def _call(_payload):
        return {
            "id": "msg_canned_e2e",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
    return _call


def app_headers(raw_gateway_key, session_token):
    return {"Authorization": f"Bearer {raw_gateway_key}",
            "X-Takyon-App-Session": session_token}


# ─────────────────────────── A. Product-AI HTTP gateway path ───────────────────────────

results = {
    "no_sub_refused": False,
    "sub_budget_equals_included": False,
    "pool_cap_gone": False,
    "exact_cost_no_markup": False,
    "action_unentitled_refused_no_pool": False,
}


def run_gateway_group():
    dsn = make_db()
    try:
        app = build_runtime_app(database_url=dsn)
        app.dependency_overrides[get_provider_caller] = canned_caller
        client = TestClient(app)
        conn = psycopg.connect(dsn, autocommit=True)
        try:
            slug, raw = provision_business(conn)

            # default budget opens with NO per-business pool cap (invariant 9: hard_limit NULL).
            # It may not be materialized until the first reserve; None == no pool cap either way.
            budget = get_app_budget(conn, slug)
            log(f"[A] default app_budget for {slug}: "
                f"{'<unopened>' if budget is None else f'status={budget.status!r} hard_limit_microusd={budget.hard_limit_microusd!r}'}")
            pool_is_null = budget is None or (
                budget.hard_limit_microusd is None and budget.status == "active"
            )

            # (1) NO active subscription -> refused 402, ledger untouched, no free budget.
            _u0, st0 = seed_unentitled_session_user(conn, slug)
            r0 = client.post("/internal/ai-gateway/messages", json=GENERATE_BODY,
                             headers=app_headers(raw, st0))
            ledger0 = list_usage_events(conn, slug)
            log(f"[A1] no-sub POST -> HTTP {r0.status_code} detail={r0.json().get('detail')!r} "
                f"ledger_events={len(ledger0)}")
            no_sub_refused = (
                r0.status_code == 402
                and r0.json().get("detail") == {"error": "subscription_required"}
                and ledger0 == []
            )
            results["no_sub_refused"] = no_sub_refused

            # (2) PAID subscription with included_ai_budget_microusd = N. One canned call costs
            # sonnet 3/15 microusd-per-token => 3*100 + 15*20 = 600 microusd. Pick N = exactly 2
            # calls' worth so the 1st & 2nd succeed and the 3rd is refused at the per-USER gate.
            per_call = 600
            N = per_call * 2  # 1200 microusd
            user, st1, plan_key = seed_paid_session_user(conn, slug, included_ai_budget_microusd=N)

            plan = app_entitlements.get_plan_policy(conn, slug, plan_key)
            derived = _user_monthly_budget_microusd(plan)
            log(f"[A2] plan {plan_key}: included_ai_budget_microusd={plan.included_ai_budget_microusd} "
                f"-> _user_monthly_budget_microusd={derived} (expect == N={N})")
            budget_equals_included = (
                int(plan.included_ai_budget_microusd) == N and derived == N
            )

            # spend allowed up to N: 2 successful settles of 600 each.
            spent = 0
            success_costs = []
            for i in range(2):
                rr = client.post("/internal/ai-gateway/messages", json=GENERATE_BODY,
                                 headers=app_headers(raw, st1))
                assert rr.status_code == 200, (i, rr.status_code, rr.text)
                cost = rr.json()["usage"]["actual_cost_microusd"]
                success_costs.append(cost)
                spent += cost
            log(f"[A2] two paid calls succeeded, actual_costs={success_costs} cumulative={spent} "
                f"(budget N={N})")

            # the 3rd call would push committed (1200) + 600 > N=1200 -> refused at per-user gate.
            r3 = client.post("/internal/ai-gateway/messages", json=GENERATE_BODY,
                             headers=app_headers(raw, st1))
            d3 = r3.json().get("detail", {})
            log(f"[A2] 3rd paid call -> HTTP {r3.status_code} detail={d3!r}")
            refused_at_n = (
                r3.status_code == 402
                and isinstance(d3, dict)
                and d3.get("error") == "app_user_budget_exceeded"
                and d3.get("user_monthly_limit_microusd") == N
                and d3.get("app_user_id") == user.id
            )

            # ledger must reflect ONLY the 2 completed events at exact cost (3rd reserved nothing).
            events = list_usage_events(conn, slug)
            completed = [e for e in events if e.status == "completed"]
            summary = get_usage_summary(conn, slug)
            log(f"[A2] ledger: total_events={len(events)} completed={len(completed)} "
                f"committed_microusd={summary['committed_microusd']}")

            results["sub_budget_equals_included"] = bool(budget_equals_included and refused_at_n)

            # (3) exact cost, no markup: every completed actual == 600, committed == 1200.
            exact = (
                all(c == per_call for c in success_costs)
                and all(e.actual_cost_microusd == per_call for e in completed)
                and len(completed) == 2
                and summary["committed_microusd"] == per_call * 2
            )
            log(f"[A3] exact-cost check: success_costs={success_costs} "
                f"completed_actuals={[e.actual_cost_microusd for e in completed]} "
                f"committed={summary['committed_microusd']} -> {exact}")
            results["exact_cost_no_markup"] = bool(exact)

            # pool-cap-gone: the per-business pool never gated (only the per-USER subscription
            # budget did). Confirm hard_limit stayed NULL AND the refusal was the per-user gate
            # (not an app_budget_exceeded pool error), AND no $5 (5_000_000) default ever appeared.
            budget_after = get_app_budget(conn, slug)
            after_null = budget_after is None or budget_after.hard_limit_microusd is None
            pool_gone = (
                pool_is_null
                and after_null
                and refused_at_n  # refusal came from per-user gate, not a pool cap
            )
            log(f"[A] pool-cap check: opened_null={pool_is_null} "
                f"after_null={after_null} "
                f"refused_by_per_user_gate={refused_at_n} -> {pool_gone}")
            results["pool_cap_gone"] = bool(pool_gone)
        finally:
            conn.close()
    finally:
        drop_db(dsn)


# ─────────────────────────── B. Action reserve path (real handler) ───────────────────────────

def run_action_group():
    """Drive the REAL app_actions._reserve_usage through a REAL TakyonStore on the PG rig.
    An unentitled service/null-subuser billable action reserve must be REFUSED (subscription_required)
    and must NOT fall through to a per-business pool (§3 gap #4)."""
    dsn = make_db()
    home = REPO / ".mvpe2e_home" / uuid.uuid4().hex[:8]
    home.mkdir(parents=True, exist_ok=True)
    try:
        store = takyon_core.TakyonStore(home, database_url=dsn)
        # Seed platform owner + an owned business row directly on the control plane (same shape the
        # repo gateway PG test uses). The action reserve handler only needs the `businesses` row +
        # app tables; it never reads a workspace revision, so we don't go through the
        # workspace-commit rail (which is host-config-gated and irrelevant to the money rail).
        slug = f"biz-{uuid.uuid4().hex[:8]}"
        with psycopg.connect(dsn, autocommit=True) as seed:
            ensure_platform_owner(seed)
            uid, _c, _r = provision_user_on_first_login(seed, f"auth0|{uuid.uuid4().hex}")
            seed.execute(
                "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
                (slug, "Acme", uid),
            )

        # Sub-case B1: a billable action reserve for a NULL/service caller with NO entitlement.
        # The gateway-mirror gate (_require_active_entitlement) must refuse BEFORE any hold, and
        # must NOT silently fund it from a per-business pool.
        refused = False
        refusal_msg = ""
        try:
            app_actions._reserve_usage(
                store,
                slug,
                reservation_key=f"act-{uuid.uuid4().hex}",
                app_user_id=None,           # service / null sub-user
                app_user_tier=None,
                estimate_microusd=2_000,    # billable (>0): MUST require active entitlement
                route=f"/api/takyon/apps/{slug}/actions/coach",
                metadata={"trigger": "http", "principal": "service"},
            )
        except app_actions.ActionBudgetExceeded as exc:
            refused = True
            refusal_msg = str(exc)
        except Exception as exc:  # any other exception is NOT the correct fail-closed shape
            refusal_msg = f"UNEXPECTED {type(exc).__name__}: {exc}"

        # Prove nothing was reserved: ledger empty for this business.
        with psycopg.connect(dsn, autocommit=True) as c2:
            evrows = app_usage.list_usage_events(c2, slug)
            budget = app_usage.get_app_budget(c2, slug)
        budget_hard_limit = None if budget is None else budget.hard_limit_microusd
        log(f"[B1] unentitled service action reserve -> refused={refused} "
            f"msg={refusal_msg!r} ledger_events={len(evrows)} "
            f"app_budget.hard_limit={budget_hard_limit!r} "
            f"(budget {'unopened' if budget is None else 'opened'})")

        no_pool_fallthrough = (
            refused
            and "subscription_required" in refusal_msg
            and len(evrows) == 0
            # default budget must NOT carry a per-business pool cap (no $5 / 5_000_000 default).
            and budget_hard_limit is None
        )

        # Sub-case B2 (control): an ENTITLED sub-user's action reserve DOES go through and holds
        # exactly against its plan-derived per-user budget (proving the refusal above is the
        # entitlement gate, not a blanket failure of the action path).
        entitled_ok = False
        with psycopg.connect(dsn, autocommit=True) as c3:
            user, _st, _pk = seed_paid_session_user(c3, slug, included_ai_budget_microusd=1_000_000)
        try:
            app_actions._reserve_usage(
                store,
                slug,
                reservation_key=f"act-{uuid.uuid4().hex}",
                app_user_id=user.id,
                app_user_tier="paid",
                estimate_microusd=2_000,
                route=f"/api/takyon/apps/{slug}/actions/coach",
                metadata={"trigger": "http", "principal": "session"},
            )
            with psycopg.connect(dsn, autocommit=True) as c4:
                ev2 = app_usage.list_usage_events(c4, slug)
            entitled_ok = any(e.status == "reserved" and e.app_user_id == user.id for e in ev2)
            log(f"[B2] entitled sub-user action reserve -> held={entitled_ok} events={len(ev2)}")
        except Exception as exc:
            log(f"[B2] entitled reserve unexpectedly failed: {type(exc).__name__}: {exc}")

        results["action_unentitled_refused_no_pool"] = bool(no_pool_fallthrough and entitled_ok)
    finally:
        drop_db(dsn)
        import shutil
        shutil.rmtree(home, ignore_errors=True)


def main():
    try:
        run_gateway_group()
    except Exception:
        log("[A] FATAL:\n" + traceback.format_exc())
    try:
        run_action_group()
    except Exception:
        log("[B] FATAL:\n" + traceback.format_exc())

    wired = all([
        results["no_sub_refused"],
        results["sub_budget_equals_included"],
        results["pool_cap_gone"],
        results["exact_cost_no_markup"],
        results["action_unentitled_refused_no_pool"],
    ])
    verdict = {
        "check": "grouped-e2e",
        "no_sub_refused": results["no_sub_refused"],
        "sub_budget_equals_included": results["sub_budget_equals_included"],
        "pool_cap_gone": results["pool_cap_gone"],
        "exact_cost_no_markup": results["exact_cost_no_markup"],
        "action_unentitled_refused_no_pool": results["action_unentitled_refused_no_pool"],
        "verdict": "WIRED" if wired else "NOT_WIRED",
        "evidence": " | ".join(evidence),
    }
    print("RESULT_JSON: " + json.dumps(verdict))


if __name__ == "__main__":
    main()
