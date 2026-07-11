from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_shared_tunnel_monitor_never_rebinds_an_occupied_listener():
    script = (ROOT / "scripts" / "takyon-operator-prod.sh").read_text(encoding="utf-8")

    assert 'local_tcp_port_listening "$LOCAL_SAFEBOX_PORT"' in script
    assert 'local_tcp_port_listening "$LOCAL_DASHBOARD_PORT"' in script
    dashboard_guard = script.index('if ! dashboard_tunnel_healthy; then', script.index('monitor_console_tunnels()'))
    dashboard_restart = script.index('start_managed_tunnel "Operator dashboard"', dashboard_guard)
    assert script.index('local_tcp_port_listening "$LOCAL_DASHBOARD_PORT"', dashboard_guard) < dashboard_restart


def test_prod_worker_preflight_proves_runtime_checkout_is_docker_bindable():
    script = (ROOT / "scripts" / "takyon-operator-prod.sh").read_text(encoding="utf-8")
    preflight = script.split("require_docker_for_worker() {", 1)[1].split("ensure_deno_toolchain() {", 1)[0]

    assert "docker version" in preflight
    assert "docker run --rm" in preflight
    assert 'src=$RUNTIME_DIR,dst=/takyon-runtime,readonly' in preflight
    assert "test -d /takyon-runtime/agent" in preflight
    assert "move or create the checkout under a Docker Desktop shared path" in preflight
