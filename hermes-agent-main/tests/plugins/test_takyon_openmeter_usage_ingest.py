"""Unit tests for the OpenMeter exact-cost usage-event mirror (GOAL_RULES §4 buy-not-build).

The mirror is BACKEND-only and fail-safe: it no-ops when OpenMeter is disabled, builds a
CloudEvent carrying micro-USD cost when enabled, and is idempotent on the reservation key.
These are hermetic (no network, no PG) — they stub the HTTP request seam.
"""

from __future__ import annotations

import pytest

from plugins.takyon import openmeter_backend as om


def test_ingest_usage_event_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(om, "enabled", lambda: False)
    # Even if _request_json were reachable it must NOT be called when disabled.
    called = {"n": 0}
    monkeypatch.setattr(om, "_request_json", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    assert om.ingest_usage_event(
        business_slug="acme",
        reservation_key="rk-1",
        actual_cost_microusd=1234,
    ) is False
    assert called["n"] == 0


def test_ingest_usage_event_builds_cloudevent_when_enabled(monkeypatch):
    monkeypatch.setattr(om, "enabled", lambda: True)
    captured = {}

    def _fake_request(method, path, *, payload=None, expected_status=None, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {}

    monkeypatch.setattr(om, "_request_json", _fake_request)
    ok = om.ingest_usage_event(
        business_slug="acme",
        reservation_key="rk-42",
        actual_cost_microusd=98765,
        route="ceo_tool",
        provider="anthropic",
        model="claude-x",
        app_user_id="u-1",
    )
    assert ok is True
    assert captured["method"] == "POST"
    # Kong-fronted OpenMeter ingest path (matches ingest_usage_event + the live gateway, probed 200).
    assert captured["path"] == "/openmeter/events"
    ev = captured["payload"]
    assert ev["type"] == "tk_ai_usage"
    assert ev["subject"] == om.usage_event_subject_for("acme")
    # exact micro-USD cost is the meter value (no rounding to dollars)
    assert ev["data"]["value"] == "98765"
    assert ev["data"]["route"] == "ceo_tool"
    assert ev["data"]["provider"] == "anthropic"
    # idempotency: the CloudEvent id is derived from the reservation key
    assert "rk_42" in ev["id"] or "rk-42" in ev["id"]


def test_ingest_usage_event_idempotent_id_is_stable(monkeypatch):
    monkeypatch.setattr(om, "enabled", lambda: True)
    ids = []
    monkeypatch.setattr(
        om, "_request_json", lambda *a, **k: ids.append(k["payload"]["id"]) or {}
    )
    for _ in range(2):
        om.ingest_usage_event(business_slug="acme", reservation_key="rk-9", actual_cost_microusd=1)
    assert ids[0] == ids[1]


def test_business_openmeter_authoritative_flag_is_ignored():
    """OpenMeter is mirror-only: metadata.openmeter_authority cannot promote it to access authority."""
    from plugins.takyon import core

    assert core._business_openmeter_authoritative(None) is False
    assert core._business_openmeter_authoritative({}) is False
    assert core._business_openmeter_authoritative({"metadata": None}) is False
    assert core._business_openmeter_authoritative({"metadata": {}}) is False
    assert core._business_openmeter_authoritative({"metadata": {"openmeter_authority": False}}) is False
    assert core._business_openmeter_authoritative({"metadata": {"openmeter_authority": True}}) is False
    assert core._business_openmeter_authoritative({"metadata": {"openmeter_authority": 1}}) is False
