"""Episode metrics snapshot: every RL episode records at least one quantitative metric at open
time (users / revenue / usage always; live-campaign delivery stats for its channel), best-effort
and never blocking the bet. Pure tests — DB rows, the ad-spend backend, and the business
filesystem are all faked."""

import json
from types import SimpleNamespace

from plugins.takyon import core


class _FakeRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _FakeConn:
    """Returns canned counts for the three product-counter queries."""

    def __init__(self, users=7, revenue_cents=1999, usage_events=42, raise_on=None):
        self._answers = [
            _FakeRow(n=users),
            _FakeRow(c=revenue_cents),
            _FakeRow(n=usage_events),
        ]
        self._i = 0
        self._raise_on = raise_on

    def execute(self, sql, params=()):
        if self._raise_on and self._raise_on in sql:
            raise RuntimeError("table missing")
        answer = self._answers[min(self._i, len(self._answers) - 1)]
        self._i += 1
        return SimpleNamespace(fetchone=lambda: answer)


def _fake_store(tmp_path):
    def resolve(slug, rel, sync=True):
        return tmp_path / slug / rel

    return SimpleNamespace(_resolve_business_file=resolve)


def _policy(**kw):
    base = dict(
        channel="reddit", slug="camp-1", status="active",
        last_synced_spend_cents=374, total_budget_cents=506, provider_campaign_id="123",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_snapshot_always_carries_product_counters(tmp_path):
    snap = core._episode_metrics_snapshot(_fake_store(tmp_path), _FakeConn(), "acme", None)
    assert snap["users"] == 7
    assert snap["revenue_cents"] == 1999
    assert snap["usage_events"] == 42
    assert snap["captured_at"]


def test_snapshot_attaches_channel_campaign_stats(tmp_path, monkeypatch):
    backend = SimpleNamespace(list_policies=lambda conn, slug, statuses=None: [_policy()])
    monkeypatch.setattr(core, "_business_ad_spend_backend", lambda: backend)
    syncs = tmp_path / "acme" / "metrics/reddit-ads/camp-1/syncs"
    syncs.mkdir(parents=True)
    (syncs / "a.json").write_text(json.dumps({"totals": {"impressions": 1301, "clicks": 4, "spend_usd": 3.74}}))

    snap = core._episode_metrics_snapshot(_fake_store(tmp_path), _FakeConn(), "acme", "reddit")
    camp = snap["campaigns"][0]
    assert camp["spend_cents"] == 374
    assert camp["impressions"] == 1301
    assert camp["clicks"] == 4


def test_snapshot_never_raises_when_everything_is_missing(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("no backend")

    monkeypatch.setattr(core, "_business_ad_spend_backend", boom)
    snap = core._episode_metrics_snapshot(
        _fake_store(tmp_path), _FakeConn(raise_on="app_users"), "acme", "meta"
    )
    assert snap["captured_at"]  # degraded but present, and no exception


def test_format_episode_metrics_renders_compact_suffix():
    text = core._format_episode_metrics(
        {
            "users": 7,
            "revenue_cents": 1999,
            "usage_events": 42,
            "campaigns": [{"slug": "camp-1", "spend_cents": 374, "impressions": 1301, "clicks": 4}],
        }
    )
    assert text.startswith(" [at record: ")
    assert "users=7" in text and "spend_c=374" in text and "impr=1301" in text
    assert core._format_episode_metrics(None) == ""
    assert core._format_episode_metrics({}) == ""
