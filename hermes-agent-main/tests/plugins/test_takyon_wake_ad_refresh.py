"""Pre-wake ad refresh: before the CEO turn, refresh delivery insights for LIVE + STALE reddit
campaigns so the pulse the CEO reads is current — not reliant on the agent remembering to sync.

Pure tests: the DB enumeration and the insights-sync tool are stubbed, so no rig/network is used.
Staleness is expressed relative to the real ``datetime.now`` the helper uses, via far-past vs.
very-recent synced timestamps, so the tests are time-of-day independent.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from plugins.takyon import core


def _policy(**kw):
    base = dict(channel="reddit", slug="acme", status="active", metadata={}, provider_campaign_id="")
    base.update(kw)
    return SimpleNamespace(**base)


def _iso_ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def _wire(policies, monkeypatch, *, sync_ok=True, sync_raises=False, env=None):
    calls = []
    meta_calls = []

    def fake_list(slug, *, statuses=None):
        return policies

    def fake_sync(args):
        calls.append(args)
        if sync_raises:
            raise RuntimeError("gateway down")
        return json.dumps({"success": bool(sync_ok)})

    def fake_meta_sync(args):
        meta_calls.append(args)
        if sync_raises:
            raise RuntimeError("gateway down")
        return json.dumps({"success": bool(sync_ok)})

    from plugins.takyon import meta_ads_v2

    monkeypatch.setattr(core, "_list_ad_spend_policies", fake_list)
    monkeypatch.setattr(core, "handle_business_reddit_ad_insights_sync", fake_sync)
    monkeypatch.setattr(meta_ads_v2, "handle_business_meta_ad_insights_sync", fake_meta_sync)
    monkeypatch.delenv("TAKYON_WAKE_AD_REFRESH", raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    return calls, meta_calls


def test_disabled_via_env_does_nothing(monkeypatch):
    calls, meta_calls = _wire([_policy()], monkeypatch, env={"TAKYON_WAKE_AD_REFRESH": "0"})
    out = core._refresh_stale_live_ad_campaigns("acme")
    assert out.get("disabled") is True
    assert calls == []


def test_live_never_synced_triggers_sync(monkeypatch):
    calls, meta_calls = _wire([_policy(status="active", metadata={})], monkeypatch)
    out = core._refresh_stale_live_ad_campaigns("acme")
    assert out["refreshed"] == 1
    assert out["campaigns"] == ["acme"]
    assert len(calls) == 1
    args = calls[0]
    assert args["business"] == "acme"
    assert args["slug"] == "acme"
    assert args["level"] == "campaign"
    assert "acme" in args["idempotency_key"]


def test_fresh_campaign_is_skipped(monkeypatch):
    calls, meta_calls = _wire([_policy(metadata={"insights_synced_at": _iso_ago(minutes=1)})], monkeypatch)
    out = core._refresh_stale_live_ad_campaigns("acme")
    assert out["refreshed"] == 0
    assert out["skipped"] >= 1
    assert calls == []


def test_stale_campaign_is_synced(monkeypatch):
    calls, meta_calls = _wire([_policy(metadata={"insights_synced_at": _iso_ago(days=2)})], monkeypatch)
    out = core._refresh_stale_live_ad_campaigns("acme")
    assert out["refreshed"] == 1
    assert len(calls) == 1


def test_meta_policy_dispatches_to_meta_sync(monkeypatch):
    """2026-07-04 parity: meta campaigns refresh through the meta insights sync (not skipped,
    and never through the reddit handler)."""
    calls, meta_calls = _wire(
        [_policy(channel="meta", slug="glow", metadata={}, provider_campaign_id="120210000000")],
        monkeypatch,
    )
    out = core._refresh_stale_live_ad_campaigns("acme")
    assert out["refreshed"] == 1
    assert out["campaigns"] == ["glow"]
    assert calls == []  # reddit handler untouched
    assert len(meta_calls) == 1
    args = meta_calls[0]
    assert args["business"] == "acme"
    assert args["slug"] == "glow"
    assert args["level"] == "campaign"
    assert args["object_id"] == "120210000000"


def test_mixed_channels_each_use_their_own_sync(monkeypatch):
    calls, meta_calls = _wire(
        [
            _policy(channel="reddit", slug="r1", metadata={}),
            _policy(channel="meta", slug="m1", metadata={}, provider_campaign_id="12099"),
        ],
        monkeypatch,
    )
    out = core._refresh_stale_live_ad_campaigns("acme")
    assert out["refreshed"] == 2
    assert sorted(out["campaigns"]) == ["m1", "r1"]
    assert len(calls) == 1 and calls[0]["slug"] == "r1"
    assert len(meta_calls) == 1 and meta_calls[0]["slug"] == "m1"


def test_unknown_channel_is_skipped(monkeypatch):
    calls, meta_calls = _wire([_policy(channel="x", metadata={})], monkeypatch)
    out = core._refresh_stale_live_ad_campaigns("acme")
    assert out["refreshed"] == 0
    assert calls == [] and meta_calls == []


def test_sync_failure_counts_error_not_refreshed(monkeypatch):
    _wire([_policy(metadata={})], monkeypatch, sync_ok=False)
    out = core._refresh_stale_live_ad_campaigns("acme")
    assert out["refreshed"] == 0
    assert out["errors"] == 1


def test_sync_exception_never_propagates(monkeypatch):
    _wire([_policy(metadata={})], monkeypatch, sync_raises=True)
    out = core._refresh_stale_live_ad_campaigns("acme")  # must not raise
    assert out["refreshed"] == 0
    assert out["errors"] == 1


def test_enumeration_error_returns_empty_summary(monkeypatch):
    def boom(slug, *, statuses=None):
        raise RuntimeError("business_ad_spend_policies is Postgres-only")

    monkeypatch.setattr(core, "_list_ad_spend_policies", boom)
    monkeypatch.delenv("TAKYON_WAKE_AD_REFRESH", raising=False)
    out = core._refresh_stale_live_ad_campaigns("acme")
    assert out["refreshed"] == 0
    assert out["errors"] == 0
