"""Targeted tests for the cron-wake group (no Postgres required).

Covers three cards:
  * **Wake prompt tuning** (`core._ceo_cron_prompt`): reads what changed since last wake (pulse +
    wake-history), decides the 1-2 highest-leverage moves (not a fixed checklist), bounds the wake
    to those tasks then sleeps, writes a daily summary on the last wake of the day in PST, keeps the
    outreach-lifecycle ladder, and is a user-turn wrapper over the stable ceo.md system prompt with
    skills/web toolsets.
  * **Cron scheduling / adjustment** (`policy.plan_min_wake_interval_seconds` +
    `control_api.operator_plan_name_for_business`): the plan -> minimum-cadence source of truth and
    the pure, fail-restrictive operator-plan read used by the wake-schedule write gate. (The gate
    wiring + DB behavior is proven on real Postgres in test_takyon_cron_wake_pg.py.)
  * **Daily summary (verify)**: the prompt still routes the CEO to takyon-business-metrics to write
    metrics/summary.md + metrics/wake-history.md every wake.

These are unit tests on pure surfaces. The grouped E2E (real wake-schedule write boundary on
Postgres) lives in test_takyon_cron_wake_pg.py.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from plugins.takyon import policy as takyon_policy
from plugins.takyon import control_api as takyon_control_api
from plugins.takyon.core import TakyonStore


# ── Wake prompt tuning ────────────────────────────────────────────────────────────────────────


def _store(tmp_path) -> TakyonStore:
    return TakyonStore(tmp_path)


def test_wake_prompt_reads_what_changed_since_last_wake(tmp_path):
    prompt = _store(tmp_path)._ceo_cron_prompt("demo")
    # (a) reads what changed: pulse + wake-history delta comparison.
    assert "business_calculate_pulse" in prompt
    assert "metrics/wake-history.md" in prompt
    assert "compare" in prompt.lower()


def test_wake_prompt_decides_1_to_2_highest_leverage_moves_not_a_checklist(tmp_path):
    prompt = _store(tmp_path)._ceo_cron_prompt("demo")
    assert "1-2 highest" in prompt
    # reasons from evidence, explicitly NOT a fixed checklist
    assert "do not run a fixed checklist" in prompt


def test_wake_prompt_caps_tasks_and_sleeps(tmp_path):
    prompt = _store(tmp_path)._ceo_cron_prompt("demo")
    assert "Cap this wake" in prompt
    assert "stop and sleep" in prompt
    # explicit final sleep / no-more-work bound
    assert "sleep" in prompt.lower()
    assert "do not start additional work" in prompt


def test_wake_prompt_keeps_outreach_lifecycle_ladder(tmp_path):
    prompt = _store(tmp_path)._ceo_cron_prompt("demo")
    assert "Advance the outreach lifecycle" in prompt
    assert "takyon-conversation-followup" in prompt
    assert "takyon-x" in prompt
    assert "takyon-distribution" in prompt


def test_wake_prompt_routes_daily_summary_to_business_metrics(tmp_path):
    prompt = _store(tmp_path)._ceo_cron_prompt("demo")
    assert "takyon-business-metrics" in prompt
    assert "metrics/summary.md" in prompt
    # conditional last-wake-of-day daily summary instruction is present (PST)
    assert "last wake of the day" in prompt.lower()
    assert "PST" in prompt or "Pacific" in prompt


def test_wake_prompt_toolsets_include_skills_and_web(tmp_path):
    # Wake is a user-turn wrapper over ceo.md, NOT a skill: it carries the authority toolset (the CEO
    # role owns the spendful business tools) plus skills + web for analytics/distribution/X. The
    # autonomous-wake product/destructive ban is enforced per-handler (_refuse_on_autonomous_wake),
    # not by dropping a toolset, so the toolset list is unchanged.
    assert _store(tmp_path)._ceo_cron_toolsets() == ["takyon", "takyon-authority", "web", "skills", "todo"]


# ── PST last-wake-of-day computation ──────────────────────────────────────────────────────────


def _patch_interval(monkeypatch, seconds):
    monkeypatch.setattr(
        TakyonStore, "_ceo_cron_wake_interval_seconds", lambda self, slug: seconds
    )


def test_last_wake_of_day_true_when_next_wake_crosses_pst_midnight(tmp_path, monkeypatch):
    _patch_interval(monkeypatch, 6 * 3600)  # every 6h
    store = _store(tmp_path)
    # 2026-06-17 22:00 PDT == 2026-06-18 05:00 UTC. Next wake (+6h) -> 2026-06-18 04:00 PDT = next
    # PST calendar day, so THIS is the last wake of the day.
    now = datetime(2026, 6, 18, 5, 0, tzinfo=timezone.utc)
    assert store._is_last_wake_of_day_pst("demo", now=now) is True
    prompt = store._ceo_cron_prompt("demo", now=now)
    assert "LAST WAKE OF THE DAY (PST)" in prompt


def test_last_wake_of_day_false_midday(tmp_path, monkeypatch):
    _patch_interval(monkeypatch, 6 * 3600)
    store = _store(tmp_path)
    # 2026-06-17 12:00 PDT == 2026-06-17 19:00 UTC. Next wake (+6h) -> 18:00 PDT, same PST day.
    now = datetime(2026, 6, 17, 19, 0, tzinfo=timezone.utc)
    assert store._is_last_wake_of_day_pst("demo", now=now) is False
    prompt = store._ceo_cron_prompt("demo", now=now)
    assert "LAST WAKE OF THE DAY (PST)" not in prompt
    # still carries the conditional instruction
    assert "last wake of the day" in prompt.lower()


def test_last_wake_of_day_false_without_readable_interval(tmp_path, monkeypatch):
    _patch_interval(monkeypatch, None)  # no schedule / not postgres
    store = _store(tmp_path)
    now = datetime(2026, 6, 18, 5, 0, tzinfo=timezone.utc)
    assert store._is_last_wake_of_day_pst("demo", now=now) is False


# ── Plan-gated minimum wake cadence (source of truth) ─────────────────────────────────────────


def test_plan_min_wake_interval_known_plans():
    assert takyon_policy.plan_min_wake_interval_seconds("DEV") == 60
    assert takyon_policy.plan_min_wake_interval_seconds("scale") == 3_600  # case-insensitive
    assert takyon_policy.plan_min_wake_interval_seconds("PRO") == 10_800
    assert takyon_policy.plan_min_wake_interval_seconds("STARTER") == 21_600


def test_plan_min_wake_interval_unknown_or_none_is_restrictive_default():
    # No plan / unknown plan -> the conservative 6h floor, never faster.
    assert takyon_policy.plan_min_wake_interval_seconds(None) == 21_600
    assert takyon_policy.plan_min_wake_interval_seconds("") == 21_600
    assert takyon_policy.plan_min_wake_interval_seconds("mystery-tier") == 21_600


def test_plan_min_wake_interval_env_override_per_plan(monkeypatch):
    monkeypatch.setenv("TAKYON_WAKE_MIN_INTERVAL_SECONDS__PRO", "14400")
    assert takyon_policy.plan_min_wake_interval_seconds("PRO") == 14_400


def test_plan_min_wake_interval_env_override_default_floor(monkeypatch):
    monkeypatch.setenv("TAKYON_WAKE_MIN_INTERVAL_SECONDS_DEFAULT", "43200")
    assert takyon_policy.plan_min_wake_interval_seconds(None) == 43_200
    assert takyon_policy.plan_min_wake_interval_seconds("mystery") == 43_200


def test_plan_min_wake_interval_clamps_floor_to_60s(monkeypatch):
    monkeypatch.setenv("TAKYON_WAKE_MIN_INTERVAL_SECONDS__DEV", "5")
    assert takyon_policy.plan_min_wake_interval_seconds("DEV") == 60


# ── operator_plan_name_for_business (pure, fail-restrictive read) ──────────────────────────────


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """Minimal stand-in: returns a fixed single-row result for the owner-status query."""

    def __init__(self, row):
        self._row = row
        self.queries: list[str] = []

    def execute(self, sql, params=None):
        self.queries.append(sql)
        return _FakeCursor(self._row)


def test_operator_plan_name_active_subscription_returns_fallback_plan(monkeypatch):
    monkeypatch.setenv("TAKYON_OPERATOR_DEFAULT_PLAN_NAME", "PRO")
    conn = _FakeConn(("active",))
    assert takyon_control_api.operator_plan_name_for_business(conn, "demo") == "PRO"
    # pure read: a single SELECT, no writes
    assert len(conn.queries) == 1
    assert conn.queries[0].strip().lower().startswith("select")


def test_operator_plan_name_trialing_subscription_returns_plan(monkeypatch):
    monkeypatch.setenv("TAKYON_OPERATOR_DEFAULT_PLAN_NAME", "STARTER")
    conn = _FakeConn(("trialing",))
    assert takyon_control_api.operator_plan_name_for_business(conn, "demo") == "STARTER"


def test_operator_plan_name_no_active_subscription_is_none():
    # canceled / none -> None so the caller applies the most-restrictive floor (downgrade tightens).
    assert takyon_control_api.operator_plan_name_for_business(_FakeConn(("canceled",)), "demo") is None
    assert takyon_control_api.operator_plan_name_for_business(_FakeConn(("none",)), "demo") is None
    assert takyon_control_api.operator_plan_name_for_business(_FakeConn((None,)), "demo") is None


def test_operator_plan_name_unknown_business_is_none():
    # no business / no owner row -> None (restrictive).
    assert takyon_control_api.operator_plan_name_for_business(_FakeConn(None), "ghost") is None
