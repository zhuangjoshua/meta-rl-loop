import os
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PROD_SCRIPT = ROOT / "scripts" / "takyon-operator-prod.sh"


def _script_source() -> str:
    return PROD_SCRIPT.read_text(encoding="utf-8")


def _tunnel_function_source() -> str:
    script = _script_source()
    return script[
        script.index("tunnel_restart_lock_path() {") : script.index("\nmonitor_console_tunnels() {")
    ]


def test_product_edge_deploy_is_clean_published_and_secret_isolated():
    script = _script_source()
    command = script[
        script.index("cmd_product_edge_deploy() {") : script.index("\ncmd_overview() {")
    ]

    assert 'git -C "$ROOT" status --porcelain --untracked-files=all' in command
    assert 'git -C "$ROOT" fetch --quiet origin main' in command
    assert 'git -C "$ROOT" rev-parse refs/remotes/origin/main' in command
    assert '[[ "$head" == "$published" ]]' in command
    assert "CLOUDFLARE_API_TOKEN is intentionally NOT vendable through /v1/env" in command
    assert "safebox.read_env_backed_value('CLOUDFLARE_API_TOKEN')" in command
    assert '"ssh",' in command
    assert '"IdentitiesOnly=yes"' in command
    assert '"BatchMode=yes"' in command
    assert "except (OSError, subprocess.TimeoutExpired)" in command
    assert '"/opt/takyon/venvs/safebox-current/bin/python -"' in command
    assert "load_dotenv(path, override=True)" in command
    assert "('/opt/takyon/.takyon/.env', '/opt/takyon/secrets/.env')" in command
    assert "first_env_backed_value" not in command
    assert "load_operator_env" not in command
    assert "require_tunnel" not in command
    assert 'env["CLOUDFLARE_API_TOKEN"] = token' in command
    assert 'safe_names = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")' in command
    assert '"wrangler@4.110.0",' in command
    assert command.count('"versions",') == 2
    assert command.count('"--config",\n                    config_path,') == 2
    assert '"upload",\n                    "--config",' in command
    assert '"deploy",\n                    "--config",' in command
    assert '"--version-tag",\n                    source_revision,' in command
    assert '"--percentage",\n                    "100",' in command
    assert '"archive",\n            "--format=tar",\n            source_revision,' in command
    assert 'with tempfile.TemporaryDirectory(prefix="takyon-product-edge-")' in command
    assert '"deploy/cloudflare/product-worker/worker.js": "worker.js"' in command
    assert '"deploy/cloudflare/product-worker/wrangler.toml": "wrangler.toml"' in command
    assert "def run_bounded(" in command
    assert "start_new_session=True" in command
    assert "except BaseException:" in command
    assert "os.killpg(process.pid, signal.SIGTERM)" in command
    assert "os.killpg(process.pid, signal.SIGKILL)" in command
    assert "signal.signal(signal.SIGTERM, _handle_term)" in command
    assert "origin/main or HEAD moved during edge upload" in command
    assert '"rev-parse", "HEAD"' in command
    assert command.index('"upload",\n                    "--config",') < command.index(
        '["git", "-C", str(repo_root), "fetch"'
    ) < command.index('"deploy",\n                    "--config",')
    assert '"triggers",\n                    "deploy",' not in command
    assert "print(token)" not in command
    assert "TAKYON_OPERATOR_DATABASE_URL" not in command
    assert "TAKYON_SAFEBOX_OPERATOR_TOKEN" not in command
    assert "product-edge-deploy)" in script
    assert 'cmd_product_edge_deploy "$@"' in script

    python_source = command.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    compile(python_source, "product-edge-deploy-heredoc", "exec")


def test_product_edge_config_disables_workers_dev_without_owning_routes():
    config = (ROOT / "deploy" / "cloudflare" / "product-worker" / "wrangler.toml").read_text(
        encoding="utf-8"
    )
    active_lines = [
        line.strip()
        for line in config.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert "workers_dev = false" in active_lines
    assert not any(line.startswith(("route =", "routes =", "[[routes]]")) for line in active_lines)


def test_prod_compute_is_sealed_to_clean_published_live_release():
    script = _script_source()
    fetch = script[
        script.index("fetch_operator_env_exports() {") : script.index(
            "\nverify_local_runtime_release() {"
        )
    ]
    verify = script[
        script.index("verify_local_runtime_release() {") : script.index("\nload_operator_env() {")
    ]
    load = script[script.index("load_operator_env() {") : script.index("\nunset_raw_runtime_authority_env() {")]

    assert "/opt/takyon/hermes-agent-main/.takyon-deploy-artifact.json" in fetch
    assert "env['TAKYON_RUNTIME_RELEASE_SHA'] = release_sha" in fetch
    assert "status --porcelain --untracked-files=all" in verify
    assert "hermes-agent-main takyon scripts/takyon-operator-prod.sh" in verify
    assert 'fetch --quiet origin main' in verify
    assert '[[ "$head" == "$published" ]]' in verify
    assert '[[ "$head" == "$deployed" ]]' in verify
    assert 'export TAKYON_RUNTIME_RELEASE_SHA="$head"' in verify
    assert "verify_local_runtime_release" in load


def test_shared_tunnel_monitor_and_initial_start_use_one_reconciler():
    script = _script_source()
    monitor = script[
        script.index("monitor_console_tunnels() {") : script.index("\nWORKER_TUNNEL_GUARD_MONITOR_PID=")
    ]

    assert "restart_managed_tunnel_if_unowned" not in script
    assert monitor.count("ensure_managed_tunnel \\") == 2
    assert "managed_tunnel_recorded_owner_owns_listener" in script
    assert "occupied by an unowned listener" in script
    assert 'nohup "$0" "$command" </dev/null' in script
    assert "register_managed_tunnel_consumer_locked" in script
    assert "exact_tracked_tunnel_listener_pid" in script
    assert '[[ "$actual" == "$expected"' in script
    assert '"$executable" == "$(command -v ssh)"' in script
    assert 'adopt_exact_tracked_tunnel_locked "$command" "$port" "$pid_file"' in script


def test_shared_tunnel_restart_lock_has_one_live_owner(tmp_path):
    script = _script_source()
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
    script = _script_source()
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


def test_concurrent_initial_tunnel_ensure_starts_exactly_one_listener(tmp_path):
    harness = tmp_path / "ensure-harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + _tunnel_function_source()
        + "\nfake_health() { [[ -f \"$STATE/healthy\" ]]; }\n"
        + "local_tcp_port_listening() { [[ -f \"$STATE/listening\" ]]; }\n"
        + "start_managed_tunnel() {\n"
        + "  if ! mkdir \"$STATE/starting\" 2>/dev/null; then touch \"$STATE/overlap\"; fi\n"
        + "  printf 'start\\n' >>\"$STATE/starts\"\n"
        + "  sleep 0.5\n"
        + "  touch \"$STATE/listening\" \"$STATE/healthy\"\n"
        + "  rmdir \"$STATE/starting\" 2>/dev/null || true\n"
        + "}\n"
        + "touch \"$READY/$1\"\n"
        + "while [[ ! -f \"$GO\" ]]; do sleep 0.01; done\n"
        + "ensure_managed_tunnel Test http://test/ http://test/health fake \"$STATE/$1.log\" \"$STATE/$1.pid\" fake_health 8871\n",
        encoding="utf-8",
    )
    operator_root = tmp_path / "operator"
    state = tmp_path / "state"
    ready = tmp_path / "ready"
    state.mkdir()
    ready.mkdir()
    go = tmp_path / "go"
    env = {
        **os.environ,
        "LOCAL_PROD_ROOT": str(operator_root),
        "STATE": str(state),
        "READY": str(ready),
        "GO": str(go),
        "TAKYON_TUNNEL_LOCK_WAIT_SECONDS": "5",
    }
    processes = [
        subprocess.Popen(
            ["bash", str(harness), str(index)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        for index in range(2)
    ]
    deadline = time.monotonic() + 5
    while len(list(ready.iterdir())) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(list(ready.iterdir())) == 2
    go.touch()
    results = [process.communicate(timeout=10) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], results
    assert (state / "starts").read_text().splitlines() == ["start"]
    assert not (state / "overlap").exists()


def test_unhealthy_unowned_listener_fails_without_rebind(tmp_path):
    harness = tmp_path / "unowned-harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + _tunnel_function_source()
        + "\nfake_health() { return 1; }\n"
        + "local_tcp_port_listening() { return 0; }\n"
        + "managed_tunnel_recorded_owner_owns_listener() { return 1; }\n"
        + "start_managed_tunnel() { touch \"$STATE/rebound\"; }\n"
        + "status=0\n"
        + "ensure_managed_tunnel Test http://test/ http://test/health fake \"$STATE/test.log\" \"$STATE/test.pid\" fake_health 8872 || status=$?\n"
        + "printf '%s\\n' \"$status\"\n",
        encoding="utf-8",
    )
    state = tmp_path / "state"
    state.mkdir()
    result = subprocess.run(
        ["bash", str(harness)],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "LOCAL_PROD_ROOT": str(tmp_path / "operator"),
            "STATE": str(state),
            "TAKYON_TUNNEL_LOCK_WAIT_SECONDS": "5",
        },
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "1"
    assert "occupied by an unowned listener" in result.stderr
    assert not (state / "rebound").exists()


def test_unhealthy_owned_listener_is_replaced_under_lock(tmp_path):
    harness = tmp_path / "owned-harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + _tunnel_function_source()
        + "\nfake_health() { [[ -f \"$STATE/healthy\" ]]; }\n"
        + "local_tcp_port_listening() { [[ -f \"$STATE/listening\" ]]; }\n"
        + "managed_tunnel_recorded_owner_owns_listener() { return 0; }\n"
        + "terminate_recorded_tunnel_owner() { touch \"$STATE/terminated\"; rm -f \"$STATE/listening\"; }\n"
        + "start_managed_tunnel() { touch \"$STATE/restarted\" \"$STATE/listening\" \"$STATE/healthy\"; }\n"
        + "ensure_managed_tunnel Test http://test/ http://test/health fake \"$STATE/test.log\" \"$STATE/test.pid\" fake_health 8873\n",
        encoding="utf-8",
    )
    state = tmp_path / "state"
    state.mkdir()
    (state / "listening").touch()
    result = subprocess.run(
        ["bash", str(harness)],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "LOCAL_PROD_ROOT": str(tmp_path / "operator"),
            "STATE": str(state),
            "TAKYON_TUNNEL_LOCK_WAIT_SECONDS": "5",
        },
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (state / "terminated").exists()
    assert (state / "restarted").exists()


def test_managed_tunnel_owner_record_verifies_the_exact_listener_process(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket() as client:
                if client.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("test listener did not start")

        script = _script_source()
        owner_functions = script[
            script.index("tunnel_owner_record_path() {") : script.index("\ntunnel_healthy() {")
        ]
        harness = tmp_path / "owner-harness.sh"
        harness.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            + owner_functions
            + "\nrecord_managed_tunnel_owner \"$PORT\" \"$OWNER_PID\"\n"
            + "managed_tunnel_recorded_owner_owns_listener \"$PORT\" \"$OWNER_PID\"\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            ["bash", str(harness)],
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "LOCAL_PROD_ROOT": str(tmp_path / "operator"),
                "PORT": str(port),
                "OWNER_PID": str(server.pid),
            },
            timeout=5,
            check=False,
        )

        assert result.returncode == 0, result.stderr
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_shared_tunnel_survives_last_console_lease_release(tmp_path):
    functions = _tunnel_function_source()
    harness = tmp_path / "consumer-lease-harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + functions
        + "\nsleep 30 & owner=$!\n"
        + "sleep 30 & first=$!\n"
        + "sleep 30 & second=$!\n"
        + "cleanup() { kill $owner $first $second 2>/dev/null || true; }\n"
        + "trap cleanup EXIT\n"
        + "record_managed_tunnel_owner 8891 $owner\n"
        + "acquire_tunnel_restart_lock_wait 8891\n"
        + "register_managed_tunnel_consumer_locked 8891 $first\n"
        + "register_managed_tunnel_consumer_locked 8891 $second\n"
        + "release_tunnel_restart_lock 8891\n"
        + "release_managed_tunnel_consumer 8891 $first\n"
        + "printf '%s ' \"$(managed_tunnel_consumer_count_locked 8891)\"\n"
        + "release_managed_tunnel_consumer 8891 $second\n"
        + "printf '%s ' \"$(managed_tunnel_consumer_count_locked 8891)\"\n"
        + "kill -0 $owner\n"
        + "printf 'owner-alive\\n'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "LOCAL_PROD_ROOT": str(tmp_path / "operator"),
            "TAKYON_TUNNEL_LOCK_WAIT_SECONDS": "5",
        },
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1 0 owner-alive"


def test_prod_worker_preflight_proves_runtime_checkout_is_docker_bindable():
    script = _script_source()
    preflight = script.split("require_docker_for_worker() {", 1)[1].split("ensure_deno_toolchain() {", 1)[0]

    assert "docker version" in preflight
    assert "docker run --rm" in preflight
    assert 'src=$RUNTIME_DIR,dst=/takyon-runtime,readonly' in preflight
    assert "test -d /takyon-runtime/agent" in preflight
    assert "move or create the checkout under a Docker Desktop shared path" in preflight
    assert 'export TAKYON_CLAUDE_AGENT_DOCKER_IMAGE="$worker_image"' in preflight


def test_docker_unshared_checkout_fails_before_worker_start(tmp_path):
    script = _script_source()
    require_docker = script[
        script.index("require_docker_for_worker() {") : script.index("\nworker_preflight_wait_seconds() {")
    ]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = version ]; then exit 0; fi\n"
        "if [ \"${1:-}\" = run ]; then exit 125; fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    harness = tmp_path / "docker-preflight.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + "die() { echo \"$*\" >&2; exit 1; }\n"
        + require_docker
        + "\nrequire_docker_for_worker\n"
        + "touch \"$WORKER_STARTED\"\n",
        encoding="utf-8",
    )
    started = tmp_path / "worker-started"
    result = subprocess.run(
        ["bash", str(harness)],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RUNTIME_DIR": "/private/tmp/docker-unshared/hermes-agent-main",
            "TERMINAL_ENV": "docker",
            "TAKYON_CLAUDE_AGENT_DOCKER_IMAGE": "test/image",
            "WORKER_STARTED": str(started),
        },
        timeout=5,
        check=False,
    )

    assert result.returncode == 1
    assert "Docker cannot bind-mount the runtime checkout" in result.stderr
    assert "Docker Desktop shared path" in result.stderr
    assert not started.exists()


def test_console_waits_for_explicit_worker_preflight_failure_and_surfaces_log(tmp_path):
    script = _script_source()
    handshake = script[
        script.index("worker_preflight_wait_seconds() {") : script.index("\nensure_deno_toolchain() {")
    ]
    harness = tmp_path / "worker-handshake.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + handshake
        + "\n( sleep 1.3; echo 'EXACT docker mount blocker' >>\"$LOG\"; exit 23 ) &\n"
        + "child=$!\n"
        + "status=0\nwait_for_worker_preflight \"$child\" \"$READY_FILE\" \"$LOG\" || status=$?\n"
        + "printf '%s\\n' \"$status\"\n",
        encoding="utf-8",
    )
    log = tmp_path / "worker.log"
    ready = tmp_path / "worker.ready"
    started = time.monotonic()
    result = subprocess.run(
        ["bash", str(harness)],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "LOG": str(log),
            "READY_FILE": str(ready),
            "TAKYON_WORKER_PREFLIGHT_WAIT_SECONDS": "5",
        },
        timeout=8,
        check=False,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0
    assert result.stdout.strip() == "1"
    assert elapsed >= 1.2
    assert "refusing to open an operator shell" in result.stderr
    assert "EXACT docker mount blocker" in result.stderr


def test_console_waits_past_one_second_for_worker_preflight_success(tmp_path):
    script = _script_source()
    handshake = script[
        script.index("worker_preflight_wait_seconds() {") : script.index("\nensure_deno_toolchain() {")
    ]
    harness = tmp_path / "worker-handshake-success.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + handshake
        + "\n( sleep 1.3; touch \"$READY_FILE\"; sleep 2 ) &\n"
        + "child=$!\n"
        + "status=0\nwait_for_worker_preflight \"$child\" \"$READY_FILE\" \"$LOG\" || status=$?\n"
        + "kill \"$child\" 2>/dev/null || true\nwait \"$child\" 2>/dev/null || true\n"
        + "printf '%s\\n' \"$status\"\n",
        encoding="utf-8",
    )
    log = tmp_path / "worker.log"
    ready = tmp_path / "worker.ready"
    started = time.monotonic()
    result = subprocess.run(
        ["bash", str(harness)],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "LOG": str(log),
            "READY_FILE": str(ready),
            "TAKYON_WORKER_PREFLIGHT_WAIT_SECONDS": "5",
        },
        timeout=8,
        check=False,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"
    assert elapsed >= 1.2


def test_console_wiring_requires_worker_ready_marker_before_shell():
    script = _script_source()
    console = script[script.index("cmd_console() {") : script.index("\ncmd_vps_worker() {")]
    worker = script[script.index("cmd_worker() {") : script.index("\ncmd_worker_once() {")]

    assert 'TAKYON_WORKER_READY_FILE="$worker_ready_file"' in console
    assert 'wait_for_worker_preflight "$worker_pid" "$worker_ready_file" "$worker_log"' in console
    preflight_failure = console[
        console.index('if ! wait_for_worker_preflight "$worker_pid"') : console.index(
            'rm -f "$worker_ready_file"',
            console.index('if ! wait_for_worker_preflight "$worker_pid"'),
        )
    ]
    assert preflight_failure.index("cleanup") < preflight_failure.index("trap - EXIT INT TERM")
    assert "Local worker unavailable; relying on delayed VPS worker fallback" not in console
    assert "mark_worker_preflight_ready" not in worker
    assert "TAKYON_WORKER_READY_FILE" not in worker
    assert 'tunnel_consumer_pid="$(current_shell_process_pid)"' in console
    assert "release_managed_tunnel_consumer" in console


def test_parallel_consoles_never_stop_unrelated_local_worker_pools():
    script = _script_source()
    console = script[script.index("cmd_console() {") : script.index("\ncmd_vps_worker() {")]
    worker = script[script.index("cmd_worker() {") : script.index("\ncmd_worker_once() {")]

    # Each console owns and drains only its wrapper. Global pool shutdown remains available through
    # the explicit stop-workers command, never as a startup/cleanup side effect. The owned worker is
    # ineligible for the generic five-second SIGKILL path.
    assert "stop_local_workers_background" not in worker
    assert "stop_local_workers" not in console
    assert 'gracefully_drain_worker_pid "$worker_pid"' in console
    assert 'terminate_pid "$worker_pid"' not in console
    graceful = script[
        script.index("gracefully_drain_worker_pid() {") : script.index(
            "\nlocal_worker_stop_grace_seconds() {"
        )
    ]
    assert "kill -TERM" in graceful
    assert "kill -KILL" not in graceful


def test_additional_console_shells_stay_bound_to_their_own_pool():
    script = _script_source()
    spawn = script[
        script.index("spawn_console_shell_windows() {") : script.index("\nrun_console_shell() {")
    ]

    # A second console may overwrite the discovery sidecar while these windows launch. Their
    # explicit inherited identity must therefore win over process-global discovery.
    assert 'TAKYON_WORKER_POOL_ID="$session_pool_id"' in spawn
    assert 'TAKYON_WORKER_POOL_EXCLUSIVE="$session_pool_exclusive"' in spawn


def test_tunnel_monitor_fails_loud_without_the_mac_lock_tool():
    script = _script_source()

    assert "require_tunnel_restart_lock_tool()" in script
    assert "[[ ! -x /usr/bin/shlock ]]" in script
    assert "command -v lsof" in script
    assert 'require_tunnel_restart_lock_tool || die "shared tunnel ownership preflight failed"' in script
