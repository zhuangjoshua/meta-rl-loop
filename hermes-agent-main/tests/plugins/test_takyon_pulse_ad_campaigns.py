"""Wake pulse surfaces active ad campaigns + insights staleness, so the CEO can re-sync a
live paid bet before judging it (ad delivery only enters Takyon on an explicit
business_<channel>_ad_insights_sync, never automatically).

These are pure transformation/staleness tests: the DB enumeration
(`core._list_ad_spend_policies`) is stubbed, so no Postgres rig is needed. The SQL enumeration
itself (`business_ad_spend.list_policies`) mirrors the already-exercised `get_policy` query.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from plugins.takyon import core

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def _policy(**kw):
    base = dict(
        channel="reddit",
        slug="acme",
        status="active",
        provider_campaign_id="cmp_1",
        daily_budget_cents=1000,
        total_budget_cents=5000,
        last_synced_spend_cents=0,
        metadata={},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _run(policies, monkeypatch, **env):
    captured = {}

    def fake_list(slug, *, statuses=None):
        captured["statuses"] = statuses
        return policies

    monkeypatch.setattr(core, "_list_ad_spend_policies", fake_list)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return core._pulse_active_ad_campaigns("acme", NOW), captured


def test_live_never_synced_needs_sync(monkeypatch):
    out, _ = _run([_policy(status="active", metadata={})], monkeypatch)
    assert len(out) == 1
    campaign = out[0]
    assert campaign["needs_sync"] is True
    assert campaign["insights_synced_ago"] == "never synced"
    assert campaign["insights_synced_at"] is None


def test_recent_sync_not_stale(monkeypatch):
    synced = (NOW - timedelta(hours=1)).isoformat()
    out, _ = _run([_policy(status="active", metadata={"insights_synced_at": synced})], monkeypatch)
    assert out[0]["needs_sync"] is False
    assert out[0]["insights_synced_ago"].endswith("ago")


def test_stale_sync_needs_sync(monkeypatch):
    synced = (NOW - timedelta(days=2)).isoformat()
    out, _ = _run([_policy(status="active", metadata={"insights_synced_at": synced})], monkeypatch)
    assert out[0]["needs_sync"] is True
    assert "2d" in out[0]["insights_synced_ago"]


def test_non_live_status_is_surfaced_but_not_flagged(monkeypatch):
    # reserved = funded but not launched: worth seeing, but not a re-sync candidate.
    out, _ = _run([_policy(status="reserved", metadata={})], monkeypatch)
    assert len(out) == 1
    assert out[0]["needs_sync"] is False


def test_spend_is_null_when_zero_and_real_when_positive(monkeypatch):
    out, _ = _run(
        [_policy(last_synced_spend_cents=0), _policy(slug="b", last_synced_spend_cents=4210)],
        monkeypatch,
    )
    by_slug = {c["slug"]: c for c in out}
    # None (not 0.0) so the CEO is not misled into reading "$0 spent" for an unsynced campaign.
    assert by_slug["acme"]["last_synced_spend_usd"] is None
    assert by_slug["b"]["last_synced_spend_usd"] == 42.10


def test_completed_is_excluded_via_status_filter(monkeypatch):
    _, captured = _run([], monkeypatch)
    statuses = captured["statuses"] or ()
    assert "completed" not in statuses
    assert "active" in statuses


def test_env_override_tightens_stale_threshold(monkeypatch):
    # A 30-minute-old sync is stale once the threshold is dropped to 60s.
    synced = (NOW - timedelta(minutes=30)).isoformat()
    out, _ = _run(
        [_policy(status="active", metadata={"insights_synced_at": synced})],
        monkeypatch,
        TAKYON_AD_INSIGHTS_STALE_SECONDS="60",
    )
    assert out[0]["needs_sync"] is True


def test_never_raises_when_enumeration_fails(monkeypatch):
    def boom(slug, *, statuses=None):
        raise RuntimeError("business_ad_spend_policies is Postgres-only")

    monkeypatch.setattr(core, "_list_ad_spend_policies", boom)
    assert core._pulse_active_ad_campaigns("acme", NOW) == []
