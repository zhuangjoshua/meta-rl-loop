from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_shared_tunnel_monitor_never_rebinds_an_occupied_listener():
    script = (ROOT / "scripts" / "takyon-operator-prod.sh").read_text(encoding="utf-8")

    assert 'local_tcp_port_listening "$LOCAL_SAFEBOX_PORT"' in script
    assert 'local_tcp_port_listening "$LOCAL_DASHBOARD_PORT"' in script
    dashboard_guard = script.index('if ! dashboard_tunnel_healthy; then', script.index('monitor_console_tunnels()'))
    dashboard_restart = script.index('start_managed_tunnel "Operator dashboard"', dashboard_guard)
    assert script.index('local_tcp_port_listening "$LOCAL_DASHBOARD_PORT"', dashboard_guard) < dashboard_restart
