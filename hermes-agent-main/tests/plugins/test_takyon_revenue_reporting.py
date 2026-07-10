from __future__ import annotations

from datetime import datetime, timezone
import inspect
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from plugins.takyon import core
from plugins.takyon import display_metrics


ROOT = Path(__file__).resolve().parents[3]


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _TractionConn(core._PGConn):
    def __init__(self, revenue_rows):
        self.revenue_rows = revenue_rows
        self.revenue_query = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        if "FROM app_revenue_events" in sql:
            self.revenue_query = (sql, tuple(params))
            return _Rows(self.revenue_rows)
        return _Rows([])


class _TractionStore:
    def __init__(self, conn):
        self.conn = conn

    def _connect(self):
        return self.conn

    @staticmethod
    def _row_to_dict(row):
        return dict(row)


def test_revenue_environment_defaults_fail_closed_to_test(monkeypatch):
    monkeypatch.delenv("TAKYON_STRIPE_MODE", raising=False)
    assert core._stripe_revenue_environment() == "test"
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "unexpected")
    assert core._stripe_revenue_environment() == "test"
    monkeypatch.setenv("TAKYON_STRIPE_MODE", " LIVE ")
    assert core._stripe_revenue_environment() == "live"


def test_traction_filters_live_revenue_and_nets_reversals(monkeypatch):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setattr(core, "_analytics_umami_config", lambda: {"enabled": False})
    monkeypatch.setattr(display_metrics, "enabled", lambda *_args, **_kwargs: False)
    now = datetime.now(timezone.utc).isoformat()
    conn = _TractionConn(
        [
            {"occurred_at": now, "amount_paid_cents": 1_000, "revenue_type": "checkout"},
            {"occurred_at": now, "amount_paid_cents": 250, "revenue_type": "reversal"},
        ]
    )

    result = core.TakyonStore.traction_timeseries(_TractionStore(conn), "acme", range_key="D")

    assert result["totals"]["revenue_cents"] == 750
    assert sum(point["revenue_cents"] for point in result["points"]) == 750
    sql, params = conn.revenue_query
    assert "COALESCE(metadata->>'stripe_environment', 'test') = ?" in sql
    assert params[-1] == "live"


def test_sqlite_revenue_reader_uses_metadata_json_without_cross_environment_leak(
    monkeypatch,
):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setattr(core, "_analytics_umami_config", lambda: {"enabled": False})
    monkeypatch.setattr(display_metrics, "enabled", lambda *_args, **_kwargs: False)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE app_revenue_events (
          business_slug TEXT, occurred_at TEXT, amount_paid_cents INTEGER,
          revenue_type TEXT, metadata_json TEXT
        );
        CREATE TABLE app_users (business_slug TEXT, created_at TEXT);
        CREATE TABLE app_usage_events (business_slug TEXT, created_at TEXT);
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "INSERT INTO app_revenue_events VALUES (?, ?, ?, ?, ?)",
        [
            ("acme", now, 1_000, "checkout", '{"stripe_environment":"live"}'),
            ("acme", now, 250, "reversal", '{"stripe_environment":"live"}'),
            ("acme", now, 99_000, "checkout", '{"stripe_environment":"test"}'),
            ("acme", now, 88_000, "checkout", "{}"),
        ],
    )

    result = core.TakyonStore.traction_timeseries(_TractionStore(conn), "acme", range_key="D")

    assert result["totals"]["revenue_cents"] == 750


def test_all_core_revenue_readers_are_environment_scoped_and_net_reversals():
    sources = {
        "app summary": inspect.getsource(core.TakyonStore._app_summary),
        "pulse": inspect.getsource(core.TakyonStore.calculate_pulse),
        "traction": inspect.getsource(core.TakyonStore.traction_timeseries),
        "account": inspect.getsource(core.handle_business_read_app_account),
        "episode": inspect.getsource(core._episode_metrics_snapshot),
    }
    for label, source in sources.items():
        assert "_stripe_revenue_environment()" in source, label
        assert "_stripe_revenue_environment_predicate(conn)" in source, label

    predicate_source = inspect.getsource(core._stripe_revenue_environment_predicate)
    assert "isinstance(conn, _PGConn)" in predicate_source
    assert "metadata->>'stripe_environment'" in predicate_source
    assert "json_extract(metadata_json" in predicate_source

    for label in ("app summary", "pulse", "account", "episode"):
        assert "revenue_type = 'reversal'" in sources[label], label
        assert "-amount_paid_cents" in sources[label], label
    assert '== "reversal"' in sources["traction"]


def test_episode_revenue_query_uses_selected_environment(monkeypatch, tmp_path):
    class _Conn:
        def __init__(self):
            self.revenue_params = None
            self.calls = 0

        def execute(self, sql, params=()):
            self.calls += 1
            if "FROM app_revenue_events" in sql:
                self.revenue_params = tuple(params)
                row = {"c": 725}
            elif "conversation_messages" in sql:
                row = {"inbound": 0, "unresolved": 0}
            else:
                row = {"n": 0}
            return SimpleNamespace(fetchone=lambda: row)

    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    conn = _Conn()
    store = SimpleNamespace(
        _resolve_business_file=lambda slug, rel, sync=True: tmp_path / slug / rel
    )

    snapshot = core._episode_metrics_snapshot(store, conn, "acme", None)

    assert snapshot["revenue_cents"] == 725
    assert conn.revenue_params == ("acme", "live")


def test_production_operator_cli_rails_pin_live_revenue_mode():
    vps_launcher = (ROOT / "deploy/argon-alpha-14/takyon-op").read_text()
    local_launcher = (ROOT / "scripts/takyon-operator-prod.sh").read_text()

    assert "stripe_mode=\"$(service_env_value TAKYON_STRIPE_MODE)\"" in vps_launcher
    assert '[[ "$stripe_mode" == "live" ]]' in vps_launcher
    assert "export TAKYON_ENV=prod" in vps_launcher
    assert 'export TAKYON_STRIPE_MODE="$stripe_mode"' in vps_launcher

    prod_load = local_launcher.split("load_operator_env() {", 1)[1].split(
        "unset_raw_runtime_authority_env() {", 1
    )[0]
    assert "'TAKYON_STRIPE_MODE': 'live'" in local_launcher
    assert "export TAKYON_ENV=prod" in prod_load
    assert "export TAKYON_STRIPE_MODE=live" in prod_load
