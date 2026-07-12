import os
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_shared_tunnel_monitor_never_rebinds_an_occupied_listener():
    script = (ROOT / "scripts" / "takyon-operator-prod.sh").read_text(encoding="utf-8")

    assert 'local_tcp_port_listening "$LOCAL_SAFEBOX_PORT"' in script
    assert 'local_tcp_port_listening "$LOCAL_DASHBOARD_PORT"' in script
    dashboard_guard = script.index('if ! dashboard_tunnel_healthy; then', script.index('monitor_console_tunnels()'))
    dashboard_restart = script.index('restart_managed_tunnel_if_unowned', dashboard_guard)
    assert script.index('local_tcp_port_listening "$LOCAL_DASHBOARD_PORT"', dashboard_guard) < dashboard_restart


def test_shared_tunnel_restart_lock_has_one_live_owner(tmp_path):
    script = (ROOT / "scripts" / "takyon-operator-prod.sh").read_text(encoding="utf-8")
    functions = script[
        script.index("tunnel_restart_lock_path() {") : script.index("\ntunnel_healthy() {")
    ]
    harness = tmp_path / "lock-harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + functions
        + "\nif acquire_tunnel_restart_lock 8765; then "
        + "printf 'owner\\n'; sleep 0.4; release_tunnel_restart_lock 8765; "
        + "else printf 'blocked\\n'; fi\n",
        encoding="utf-8",
    )
    env = {**os.environ, "LOCAL_PROD_ROOT": str(tmp_path / "operator")}
    first = subprocess.Popen(["bash", str(harness)], text=True, stdout=subprocess.PIPE, env=env)
    second = subprocess.Popen(["bash", str(harness)], text=True, stdout=subprocess.PIPE, env=env)
    first_out, _ = first.communicate(timeout=5)
    second_out, _ = second.communicate(timeout=5)

    assert first.returncode == 0
    assert second.returncode == 0
    assert sorted([first_out.strip(), second_out.strip()]) == ["blocked", "owner"]


def test_shared_tunnel_restart_lock_reclaims_stale_owner_once_under_contention(tmp_path):
    script = (ROOT / "scripts" / "takyon-operator-prod.sh").read_text(encoding="utf-8")
    functions = script[
        script.index("tunnel_restart_lock_path() {") : script.index("\ntunnel_healthy() {")
    ]
    harness = tmp_path / "stale-lock-harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + functions
        + "\ntouch \"$READY_DIR/$1\"\n"
        + "while [[ ! -e \"$START_FILE\" ]]; do sleep 0.01; done\n"
        + "status=0\nacquire_tunnel_restart_lock 8766 || status=$?\n"
        + "touch \"$ATTEMPTED_DIR/$1\"\n"
        + "if [[ \"$status\" == 0 ]]; then\n"
        + "  for _ in $(seq 1 200); do\n"
        + "    count=$(find \"$ATTEMPTED_DIR\" -type f | wc -l | tr -d '[:space:]')\n"
        + "    [[ \"$count\" -ge \"$EXPECTED\" ]] && break\n"
        + "    sleep 0.01\n"
        + "  done\n"
        + "  printf 'owner\\n'\nrelease_tunnel_restart_lock 8766\n"
        + "elif [[ \"$status\" == 1 ]]; then printf 'blocked\\n'\n"
        + "else printf 'error:%s\\n' \"$status\"; exit \"$status\"\nfi\n",
        encoding="utf-8",
    )
    operator_root = tmp_path / "operator"
    lock_root = operator_root / "tunnel-locks"
    ready = tmp_path / "ready"
    attempted = tmp_path / "attempted"
    lock_root.mkdir(parents=True)
    ready.mkdir()
    attempted.mkdir()
    stale_lock = lock_root / "restart-8766.lock"
    stale_lock.write_text("999999\n", encoding="utf-8")
    stale_time = time.time() - 10
    os.utime(stale_lock, (stale_time, stale_time))
    # shlock deliberately refuses to reap a lock whose inode metadata changed in the current
    # second, which closes the exact stale-observer race under test.
    time.sleep(1.1)
    contender_count = 8
    start = tmp_path / "start"
    env = {
        **os.environ,
        "LOCAL_PROD_ROOT": str(operator_root),
        "READY_DIR": str(ready),
        "ATTEMPTED_DIR": str(attempted),
        "START_FILE": str(start),
        "EXPECTED": str(contender_count),
    }
    processes = [
        subprocess.Popen(
            ["bash", str(harness), str(index)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        for index in range(contender_count)
    ]
    deadline = time.monotonic() + 5
    while len(list(ready.iterdir())) < contender_count and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(list(ready.iterdir())) == contender_count
    start.touch()
    results = [process.communicate(timeout=8) for process in processes]
    outputs = [stdout.strip() for stdout, _stderr in results]

    assert [process.returncode for process in processes] == [0] * contender_count, results
    assert outputs.count("owner") == 1
    assert outputs.count("blocked") == contender_count - 1


def test_prod_worker_preflight_proves_runtime_checkout_is_docker_bindable():
    script = (ROOT / "scripts" / "takyon-operator-prod.sh").read_text(encoding="utf-8")
    preflight = script.split("require_docker_for_worker() {", 1)[1].split("ensure_deno_toolchain() {", 1)[0]

    assert "docker version" in preflight
    assert "docker run --rm" in preflight
    assert 'src=$RUNTIME_DIR,dst=/takyon-runtime,readonly' in preflight
    assert "test -d /takyon-runtime/agent" in preflight
    assert "move or create the checkout under a Docker Desktop shared path" in preflight


def test_tunnel_monitor_fails_loud_without_the_mac_lock_tool():
    script = (ROOT / "scripts" / "takyon-operator-prod.sh").read_text(encoding="utf-8")

    assert "require_tunnel_restart_lock_tool()" in script
    assert "[[ ! -x /usr/bin/shlock ]]" in script
    assert 'require_tunnel_restart_lock_tool || die "shared tunnel ownership preflight failed"' in script
