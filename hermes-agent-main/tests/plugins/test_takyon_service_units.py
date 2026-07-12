import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _unset_environment_names(unit_path: str) -> set[str]:
    names: set[str] = set()
    text = (ROOT / unit_path).read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("UnsetEnvironment="):
            continue
        _, values = stripped.split("=", 1)
        names.update(value for value in values.split() if value)
    return names


def test_runtime_units_do_not_inherit_capability_signing_key():
    for unit_path in (
        "deploy/argon-alpha-14/takyon-dashboard.service",
        "deploy/argon-alpha-14/takyon-worker.service",
        "deploy/argon-alpha-14/takyon-docker-broker.service",
        "deploy/takyon-subuser/takyon-subuser.service",
    ):
        assert "TAKYON_CAP_SIGNING_KEY" in _unset_environment_names(unit_path), unit_path


def test_docker_broker_does_not_inherit_operator_safebox_token():
    names = _unset_environment_names("deploy/argon-alpha-14/takyon-docker-broker.service")
    assert "TAKYON_SAFEBOX_OPERATOR_TOKEN" in names


def test_safebox_unit_keeps_capability_signing_authority():
    names = _unset_environment_names("deploy/takyon-safebox/takyon-safebox.service")
    assert "TAKYON_CAP_SIGNING_KEY" not in names


def test_production_runtime_units_pin_stripe_live_mode():
    for unit_path in (
        "deploy/argon-alpha-14/takyon-dashboard.service",
        "deploy/argon-alpha-14/takyon-worker.service",
        "deploy/takyon-safebox/takyon-safebox.service",
        "deploy/takyon-subuser/takyon-subuser.service",
    ):
        lines = set((ROOT / unit_path).read_text().splitlines())
        assert "Environment=TAKYON_ENV=prod" in lines, unit_path
        assert "Environment=TAKYON_STRIPE_MODE=live" in lines, unit_path


def test_operator_units_pin_ceo_to_openai_and_worker_to_deepseek():
    required = {
        "Environment=TAKYON_STRICT_MODEL_ROLES=1",
        "Environment=TAKYON_MODEL=gpt-5.5",
        "Environment=TAKYON_CLAUDE_AGENT_MODEL=deepseek-v4-pro",
        "Environment=ANTHROPIC_MODEL=deepseek-v4-pro",
        "Environment=ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-pro",
        "Environment=ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-pro",
        "Environment=ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-pro",
        "Environment=CLAUDE_CODE_SUBAGENT_MODEL=deepseek-v4-pro",
    }
    for unit_path in (
        "deploy/argon-alpha-14/takyon-dashboard.service",
        "deploy/argon-alpha-14/takyon-worker.service",
        "deploy/takyon-dev-split/takyon-dashboard-dev.service.tmpl",
        "deploy/takyon-dev-split/takyon-worker-dev.service.tmpl",
    ):
        lines = set((ROOT / unit_path).read_text().splitlines())
        assert required <= lines, unit_path


def test_runtime_units_strip_wrong_plane_and_migration_database_urls():
    expectations = {
        "deploy/argon-alpha-14/takyon-dashboard.service": {
            "TAKYON_APP_DATABASE_URL",
            "TAKYON_SAFEBOX_DATABASE_URL",
            "TAKYON_MIGRATION_DATABASE_URL",
            "MIGRATION_DATABASE_URL",
            "TAKYON_RUNTIME_DATABASE_URL",
        },
        "deploy/argon-alpha-14/takyon-worker.service": {
            "TAKYON_APP_DATABASE_URL",
            "TAKYON_SAFEBOX_DATABASE_URL",
            "TAKYON_MIGRATION_DATABASE_URL",
            "MIGRATION_DATABASE_URL",
            "TAKYON_RUNTIME_DATABASE_URL",
        },
        "deploy/argon-alpha-14/takyon-docker-broker.service": {
            "TAKYON_OPERATOR_DATABASE_URL",
            "TAKYON_APP_DATABASE_URL",
            "TAKYON_SAFEBOX_DATABASE_URL",
            "TAKYON_MIGRATION_DATABASE_URL",
            "MIGRATION_DATABASE_URL",
            "TAKYON_RUNTIME_DATABASE_URL",
        },
        "deploy/takyon-subuser/takyon-subuser.service": {
            "TAKYON_OPERATOR_DATABASE_URL",
            "TAKYON_SAFEBOX_DATABASE_URL",
            "TAKYON_MIGRATION_DATABASE_URL",
            "MIGRATION_DATABASE_URL",
            "TAKYON_RUNTIME_DATABASE_URL",
        },
        "deploy/takyon-safebox/takyon-safebox.service": {
            "DATABASE_URL",
            "POSTGRES_URL",
            "POSTGRES_PRISMA_URL",
            "POSTGRES_URL_NON_POOLING",
            "TAKYON_OPERATOR_DATABASE_URL",
            "TAKYON_APP_DATABASE_URL",
            "TAKYON_MIGRATION_DATABASE_URL",
            "MIGRATION_DATABASE_URL",
            "TAKYON_RUNTIME_DATABASE_URL",
        },
    }
    for unit_path, required_unsets in expectations.items():
        names = _unset_environment_names(unit_path)
        assert required_unsets <= names, unit_path


def test_authority_env_validator_is_host_specific_and_secret_safe():
    src = (ROOT / "deploy/shared/validate-authority-env.sh").read_text()

    assert "Usage:" in src
    assert "never prints secret" in src
    assert "reject_legacy_database_urls" in src
    assert "reject_key DATABASE_URL" in src
    assert "operator)" in src
    assert "require_key TAKYON_OPERATOR_DATABASE_URL" in src
    assert "reject_key TAKYON_APP_DATABASE_URL" in src
    assert "reject_key TAKYON_SAFEBOX_DATABASE_URL" in src
    assert "require_key TAKYON_SAFEBOX_OPERATOR_TOKEN" in src
    assert "subuser)" in src
    assert "require_key TAKYON_APP_DATABASE_URL" in src
    assert "reject_key TAKYON_OPERATOR_DATABASE_URL" in src
    assert "reject_key TAKYON_SAFEBOX_OPERATOR_TOKEN" in src
    assert "safebox)" in src
    assert "require_key TAKYON_SAFEBOX_DATABASE_URL" in src
    assert "require_key TAKYON_MIGRATION_DATABASE_URL" in src
    assert "reject_key MIGRATION_DATABASE_URL" in src
    assert "reject_key TAKYON_RUNTIME_DATABASE_URL" in src


def test_deploy_scripts_preflight_authority_env_before_sync_or_restart():
    scripts = {
        "operator": ROOT / "deploy/argon-alpha-14/deploy-runtime.sh",
        "subuser": ROOT / "deploy/takyon-subuser/deploy-runtime.sh",
        "safebox": ROOT / "deploy/takyon-safebox/deploy-runtime.sh",
    }

    for plane, path in scripts.items():
        src = path.read_text()
        assert "VALIDATE_AUTHORITY_ENV_SCRIPT" in src, plane
        assert f"bash -s -- {plane} /opt/takyon/.takyon/.env /opt/takyon/secrets/.env" in src, plane
        preflight_index = src.index(f"bash -s -- {plane}")
        assert preflight_index < src.index("takyon_stage_runtime_release"), plane


def test_runtime_deploys_fail_closed_on_venv_symlinks_and_protect_remote_venvs():
    release_helper = (ROOT / "deploy/shared/runtime-release.sh").read_text()
    scripts = (
        ROOT / "deploy/argon-alpha-14/deploy-runtime.sh",
        ROOT / "deploy/takyon-subuser/deploy-runtime.sh",
        ROOT / "deploy/takyon-safebox/deploy-runtime.sh",
    )
    for path in scripts:
        src = path.read_text()
        preflight = src.index('if [[ -L "$RUNTIME_DIR/.venv" ]]')
        assert preflight < src.index("ssh -i"), path
        assert 'source "$RUNTIME_RELEASE_SCRIPT"' in src, path
        assert "takyon_stage_runtime_release" in src, path
        assert "rsync -az --delete --force" not in src, path
    assert "--filter='protect /.venv'" in release_helper
    assert "--exclude '/.venv'" in release_helper


def test_runtime_rsync_rules_cannot_replace_remote_venv_with_source_symlink(tmp_path):
    rsync = shutil.which("rsync")
    if not rsync:
        return
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "payload.txt").write_text("new\n")
    (source / "elsewhere").mkdir()
    (source / ".venv").symlink_to(source / "elsewhere", target_is_directory=True)
    (target / ".venv" / "bin").mkdir(parents=True)
    marker = target / ".venv" / "bin" / "python"
    marker.write_text("production-venv\n")

    subprocess.run(
        [
            rsync,
            "-a",
            "--delete",
            "--filter=protect /.venv",
            "--exclude=/.venv",
            f"{source}/",
            f"{target}/",
        ],
        check=True,
    )

    assert marker.read_text() == "production-venv\n"
    assert (target / "payload.txt").read_text() == "new\n"


def test_safebox_deploy_uses_hash_locked_external_environment_before_restart():
    deploy = (ROOT / "deploy/takyon-safebox/deploy-runtime.sh").read_text()
    rebuild = (ROOT / "deploy/takyon-safebox/rebuild-venv.sh").read_text()
    bootstrap = (ROOT / "deploy/takyon-safebox/bootstrap-host.sh").read_text()
    unit = (ROOT / "deploy/takyon-safebox/takyon-safebox.service").read_text()
    dev_unit = (ROOT / "deploy/takyon-dev-split/takyon-safebox-dev.service.tmpl").read_text()

    assert "hermes-agent-main/.venv" in bootstrap
    assert '"$REBUILD_VENV_SCRIPT"' in bootstrap
    assert "from tools.lazy_deps import ensure" not in deploy
    assert "-m pip check" in deploy
    assert deploy.index('"$REBUILD_VENV_SCRIPT"') < deploy.index("systemctl restart")
    assert "--require-hashes" in rebuild
    assert "--only-binary=:all:" in rebuild
    assert 'TAKYON_SAFEBOX_VENV_ACTIVATE=0' in deploy
    assert 'TAKYON_SAFEBOX_VENV_RESULT_FILE="$remote_venv_result"' in deploy
    assert 'if [[ "$current_target" == "$candidate" ]]' in rebuild
    assert "refusing in-place mutation" in rebuild
    assert '-e "$runtime"' not in rebuild
    assert deploy.index("systemctl stop '$TAKYON_REMOTE_SERVICE_NAME'") < deploy.index(
        "safebox-current.next"
    )
    assert rebuild.index("-m pip check") < rebuild.index('mv -Tf "$current.next" "$current"')
    assert "ExecStart=/opt/takyon/venvs/safebox-current/bin/python" in unit
    assert "ExecStart=/opt/takyon/venvs/safebox-current/bin/python" in dev_unit
    assert "Environment=PUBLIC_COMPANY_BASE_DOMAIN=dev.coscale.app" in dev_unit


def test_safebox_candidate_repair_never_renames_active_environment(tmp_path):
    runtime = ROOT / "hermes-agent-main"
    lock_file = runtime / "packaging/safebox-requirements.lock"
    lock_sha = hashlib.sha256(lock_file.read_bytes()).hexdigest()
    venv_root = tmp_path / "venvs"
    base = venv_root / f"safebox-{lock_sha[:16]}"
    repair_id = "abcdef0123456789"
    repair = venv_root / f"safebox-{lock_sha[:16]}-repair-{repair_id[:12]}"
    for candidate, exit_code in ((base, 1), (repair, 0)):
        (candidate / "bin").mkdir(parents=True)
        python = candidate / "bin/python"
        python.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
        python.chmod(0o755)
        (candidate / ".takyon-safebox-lock-sha256").write_text(
            lock_sha + "\n", encoding="utf-8"
        )
    current = venv_root / "safebox-current"
    current.symlink_to(base, target_is_directory=True)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        "while [[ $# -gt 0 && $1 != bash ]]; do shift; done\n"
        "exec \"$@\"\n",
        encoding="utf-8",
    )
    fake_ssh.chmod(0o755)
    fake_install = fake_bin / "install"
    fake_install.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        "target=''\nfor target in \"$@\"; do :; done\n"
        "mkdir -p \"$target\"\n",
        encoding="utf-8",
    )
    fake_install.chmod(0o755)
    fake_flock = fake_bin / "flock"
    fake_flock.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_flock.chmod(0o755)
    key = tmp_path / "key"
    key.write_text("test\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(ROOT / "deploy/takyon-safebox/rebuild-venv.sh")],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "TAKYON_VPS_KEY": str(key),
            "TAKYON_VPS_HOST": "root@test.invalid",
            "TAKYON_REMOTE_RUNTIME": str(runtime),
            "TAKYON_REMOTE_VENV_ROOT": str(venv_root),
            "TAKYON_SAFEBOX_VENV_ACTIVATE": "0",
            "TAKYON_SAFEBOX_VENV_REPAIR_ID": repair_id,
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert current.resolve() == base.resolve()
    assert base.is_dir()
    assert not list(venv_root.glob(f"{base.name}.invalid-*"))
    assert f"candidate ready: {repair}" in result.stdout


def test_runtime_units_are_staged_before_the_quiesced_activation_window():
    operator = (ROOT / "deploy/argon-alpha-14/deploy-runtime.sh").read_text()
    subuser = (ROOT / "deploy/takyon-subuser/deploy-runtime.sh").read_text()
    safebox = (ROOT / "deploy/takyon-safebox/deploy-runtime.sh").read_text()

    for source in (operator, subuser, safebox):
        assert "$TAKYON_VPS_HOST:$TAKYON_REMOTE_SERVICE_FILE" not in source
    assert "$TAKYON_VPS_HOST:$TAKYON_REMOTE_WORKER_SERVICE_FILE" not in operator
    assert "$TAKYON_VPS_HOST:$TAKYON_REMOTE_DOCKER_BROKER_SERVICE_FILE" not in operator

    assert operator.index("$TAKYON_VPS_HOST:$remote_dashboard_candidate") < operator.index(
        "TAKYON_STOP_CORE_SERVICES=1"
    )
    assert operator.index("TAKYON_STOP_CORE_SERVICES=1") < operator.index(
        "'$remote_dashboard_candidate' '$TAKYON_REMOTE_SERVICE_FILE'"
    )
    assert "$TAKYON_VPS_HOST:$remote_service_candidate" in subuser
    assert "'$remote_service_candidate' '$TAKYON_REMOTE_SERVICE_FILE'" in subuser
    assert safebox.index("$TAKYON_VPS_HOST:$remote_service_candidate") < safebox.index(
        "systemctl stop '$TAKYON_REMOTE_SERVICE_NAME'"
    )


def test_production_safebox_deploy_verifies_exact_live_checkout_posture():
    deploy = (ROOT / "deploy/takyon-safebox/deploy-runtime.sh").read_text()

    assert 'TAKYON_EXPECT_STRIPE_CHECKOUT_DISABLED="${TAKYON_EXPECT_STRIPE_CHECKOUT_DISABLED:-0}"' in deploy
    for assignment in (
        "TAKYON_STRIPE_CHECKOUT_DISABLED=$TAKYON_EXPECT_STRIPE_CHECKOUT_DISABLED",
        "TAKYON_STRIPE_ACCOUNT_ID=acct_1TXWsW7tYL4lkVC6",
        "TAKYON_STRIPE_OPERATOR_CHECKOUT_DISABLED=1",
        "TAKYON_STRIPE_CREATIVE_CHECKOUT_DISABLED=1",
    ):
        key = assignment.split("=", 1)[0]
        assert f"grep -hE '^{key}='" in deploy
        assert f"'{assignment}'" in deploy
        assert f"grep -Fxq '{assignment}'" in deploy
    restart_tail = deploy.split("systemctl restart '$TAKYON_REMOTE_SERVICE_NAME'", 1)[1]
    assert restart_tail.index("curl -fsS http://10.116.0.2:8000/healthz") < restart_tail.index(
        "process_env="
    )
    assert r'[[ \"\$ready\" == 1 ]]' in restart_tail


def test_operator_deploy_runs_migrations_only_when_revision_requires_them():
    src = (ROOT / "deploy/argon-alpha-14/deploy-runtime.sh").read_text()

    assert 'TAKYON_RUN_DB_MIGRATIONS="${TAKYON_RUN_DB_MIGRATIONS:-0}"' in src
    assert 'if [[ "$TAKYON_RUN_DB_MIGRATIONS" != "1" ]]' in src
    assert "run_remote_migrations" in src


def test_operator_deploy_quiesces_worker_before_observing_and_migrating():
    src = (ROOT / "deploy/argon-alpha-14/deploy-runtime.sh").read_text()

    quiesce = src.index("\nquiesce_remote_worker\n")
    observe = src.index("\nwait_for_remote_runtime_idle\n")
    migrate = src.index("\nrun_remote_migrations\n")
    stop_dashboard = src.index("TAKYON_STOP_CORE_SERVICES=1")
    assert quiesce < observe < migrate < stop_dashboard
    assert "/run/systemd/system/takyon-worker.service.d/90-takyon-deploy-stop-grace.conf" in src
    assert "TimeoutStopSec=${TAKYON_DEPLOY_WORKER_STOP_GRACE_SECONDS}s" in src
    assert "systemctl daemon-reload" in src
    assert "systemctl set-property" not in src
    assert "WHERE work_request.status = 'running'" in src
    assert "WHERE work_request.status IN ('queued', 'running')" not in src
    assert "restore_operator_services_on_failure" in src
    assert "trap restore_operator_services_on_failure EXIT" in src
    assert "systemctl stop takyon-worker.service takyon-dashboard.service takyon-docker-broker.service" in src

    worker_unit = (ROOT / "deploy/argon-alpha-14/takyon-worker.service").read_text()
    assert "TimeoutStopSec=960" in worker_unit


def test_operator_deploy_drain_probe_survives_ssh_double_quoted_string():
    src = (ROOT / "deploy/argon-alpha-14/deploy-runtime.sh").read_text()

    drain = src.split("wait_for_remote_runtime_idle() {", 1)[1].split("wait_for_remote_runtime_idle", 1)[0]
    assert 'resolve_database_url(plane="operator")' not in drain
    assert 'assert_takyon_pg_role(conn, "operator")' not in drain
    assert "resolve_database_url(plane='operator')" in drain
    assert "assert_takyon_pg_role(conn, 'operator')" in drain


def test_operator_deploy_drain_ignores_only_unambiguous_mac_owned_work():
    src = (ROOT / "deploy/argon-alpha-14/deploy-runtime.sh").read_text()
    drain = src.split("wait_for_remote_runtime_idle() {", 1)[1].split("wait_for_remote_runtime_idle", 1)[0]

    assert "mac_job.payload->>'work_request_id' = work_request.id" in drain
    assert "COALESCE(mac_job.locked_by, '') LIKE 'mac-operator-%%'" in drain
    assert "other_job.payload->>'work_request_id' = work_request.id" in drain
    assert "COALESCE(other_job.locked_by, '') NOT LIKE 'mac-operator-%%'" in drain
    assert "COALESCE(locked_by, '') NOT LIKE 'mac-operator-%'" in drain


def test_operator_deploy_drain_owner_query_semantics():
    src = (ROOT / "deploy/argon-alpha-14/deploy-runtime.sh").read_text()
    match = re.search(
        r'cur\.execute\(\s*\\"\\"\\"\s*'
        r'(SELECT COUNT\(\*\)\s+FROM business_work_requests AS work_request.*?)'
        r'\s*\\"\\"\\",\s*'
        r'\(f\\"\$TAKYON_DEPLOY_ACTIVE_WORK_REQUEST_FRESHNESS_SECONDS seconds\\",\),',
        src,
        re.S,
    )
    assert match is not None
    # psycopg's bound-query parser treats every literal percent as formatting syntax. Prove the
    # embedded SQL keeps its one real %s placeholder while escaping LIKE wildcards as %%.
    assert "mac-operator-%%" in match.group(1)
    assert "mac-operator-%'" not in match.group(1).replace("mac-operator-%%", "")
    match.group(1) % ("1800 seconds",)
    query = re.sub(
        r"AND NULLIF\(work_request\.updated_at, ''\)::timestamptz >= \(NOW\(\) - %s::interval\)",
        "AND 1 = 1",
        match.group(1),
        count=1,
    )

    cases = {
        "mac-only": ([('running', 'mac-operator-local-1')], 0),
        "mixed-owner": ([('running', 'mac-operator-local-1'), ('running', 'vps-worker-1')], 1),
        # The VPS worker is already quiesced before this probe; queued work is durable and cannot
        # be killed by the restart, so only another running owner blocks activation.
        "queued-sibling": ([('running', 'mac-operator-local-1'), ('queued', None)], 0),
        "missing-link": ([], 1),
        "unknown-owner": ([('running', None)], 1),
        "terminal-sibling": ([('running', 'mac-operator-local-1'), ('completed', 'vps-worker-1')], 0),
    }
    for label, (jobs, expected) in cases.items():
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE business_work_requests (id TEXT, status TEXT, updated_at TEXT);
            CREATE TABLE jobs (payload TEXT, status TEXT, locked_by TEXT);
            INSERT INTO business_work_requests VALUES ('work-1', 'running', 'now');
            """
        )
        conn.executemany(
            "INSERT INTO jobs VALUES (?, ?, ?)",
            [
                (json.dumps({"work_request_id": "work-1"}), status, locked_by)
                for status, locked_by in jobs
            ],
        )
        assert conn.execute(query).fetchone()[0] == expected, label
        conn.close()


def test_operator_bootstrap_remote_shell_contains_no_local_command_substitutions():
    src = (ROOT / "deploy/argon-alpha-14/bootstrap-host.sh").read_text()
    remote = src.split('ssh "${target_ssh[@]}" "$TARGET_HOST" "set -euo pipefail', 1)[1].split(
        '\n"\n\n# Provision the rate_limit module', 1
    )[0]

    assert "`" not in remote


def test_operator_prod_script_targets_the_exact_active_local_worker_pool():
    src = (ROOT / "scripts/takyon-operator-prod.sh").read_text()

    load = src.split("load_operator_env() {", 1)[1].split("unset_raw_runtime_authority_env() {", 1)[0]
    worker = src.split("cmd_worker() {", 1)[1].split("cmd_worker_once() {", 1)[0]
    console = src.split("cmd_console() {", 1)[1].split("cmd_vps_worker() {", 1)[0]

    # Stage 2 (ClaimScope): the pool-id rail replaced the prefix-hint rail entirely.
    assert "TAKYON_PREFERRED_WORKER" not in src
    assert 'ACTIVE_LOCAL_WORKER_PREFIX_FILE=' in src
    assert "local_worker_pool_id_for_pid() {" in src
    assert "record_active_local_worker_pool() {" in src
    assert "active_local_worker_pool_id() {" in src
    assert "resolve_local_worker_pool_id() {" in src
    assert 'local_worker_pool="$(resolve_local_worker_pool_id)"' in load
    assert 'export TAKYON_WORKER_POOL_ID="$local_worker_pool"' in load
    assert 'worker_id="${TAKYON_WORKER_POOL_ID:-$(local_worker_pool_id_for_pid "$$")}"' in worker
    assert 'export TAKYON_WORKER_POOL_ID="$worker_id"' in worker
    assert '--worker-id "$worker_id"' in worker
    assert 'export TAKYON_WORKER_POOL_ID="$session_pool_id"' in console
    assert 'export TAKYON_WORKER_POOL_EXCLUSIVE="${TAKYON_WORKER_POOL_EXCLUSIVE:-1}"' in console
    assert 'record_active_local_worker_pool "$worker_pid" "$session_pool_id"' in console


def test_operator_prod_script_handles_empty_local_worker_pool_under_nounset():
    src = (ROOT / "scripts/takyon-operator-prod.sh").read_text()

    collect = src.split("collect_local_worker_pids() {", 1)[1].split("_wait_for_local_worker_exit() {", 1)[0]
    assert 'if [[ "${#pids[@]}" -eq 0 ]]; then' in collect
    assert "return 0" in collect
    assert "printf '%s\\n' \"${pids[@]}\"" in collect


def test_operator_prod_script_adopts_vps_model_pins_for_mac_compute():
    src = (ROOT / "scripts/takyon-operator-prod.sh").read_text()
    ensure_home = src.split("ensure_home() {", 1)[1].split("fetch_operator_env_exports() {", 1)[0]
    fetch = src.split("fetch_operator_env_exports() {", 1)[1].split("load_operator_env() {", 1)[0]

    assert 'if [[ -f "$ROOT/.takyon/config.yaml" ]]' not in ensure_home
    assert 'cat /opt/takyon/.takyon/config.yaml' in ensure_home
    assert "'TAKYON_STRICT_MODEL_ROLES': '1'" in fetch
    assert "'TAKYON_MODEL': 'gpt-5.5'" in fetch
    assert "'TAKYON_CLAUDE_AGENT_MODEL': 'deepseek-v4-pro'" in fetch
    assert "'CLAUDE_CODE_SUBAGENT_MODEL': 'deepseek-v4-pro'" in fetch


def test_operator_vps_launcher_propagates_and_validates_exact_model_pins():
    src = (ROOT / "deploy/argon-alpha-14/takyon-op").read_text()

    for key in (
        "TAKYON_STRICT_MODEL_ROLES",
        "TAKYON_MODEL",
        "TAKYON_CLAUDE_AGENT_MODEL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
    ):
        assert f"service_env_value {key}" in src
        assert f"export {key}=" in src
    assert '[[ "$ceo_model" == "gpt-5.5" ]]' in src
    assert '[[ "$model_value" == "deepseek-v4-pro" ]]' in src


def test_app_control_blocker_matches_operator_runtime_text_timestamp():
    src = (ROOT / "hermes-agent-main/plugins/takyon/db/migrations/0045_app_runtime_identity_ports.sql").read_text()

    signature = src.split("create or replace function takyon_app_control_blocker(", 1)[1].split("language sql", 1)[0]
    assert "updated_at text" in signature
    assert "updated_at timestamptz" not in signature


def test_app_service_email_sends_today_aliases_authorized_cte():
    src = (ROOT / "hermes-agent-main/plugins/takyon/db/migrations/0045_app_runtime_identity_ports.sql").read_text()

    body = src.split("create or replace function takyon_app_service_email_sends_today(", 1)[1].split("create or replace function takyon_app_visible_directory_entries(", 1)[0]
    assert "select 1 as authorized" in body
    assert "group by ss.authorized" in body


def test_app_media_usage_parameter_rename_migrations_are_idempotent():
    first = (ROOT / "hermes-agent-main/plugins/takyon/db/migrations/0045_app_runtime_identity_ports.sql").read_text()
    second = (ROOT / "hermes-agent-main/plugins/takyon/db/migrations/0051_session_bound_app_media_usage.sql").read_text()

    assert "drop function if exists takyon_app_media_usage(text, text);" in first
    assert "drop function if exists takyon_app_media_usage(text, text);" in second


def test_authority_env_validator_rejects_cross_plane_database_urls(tmp_path):
    script = ROOT / "deploy/shared/validate-authority-env.sh"
    cases = {
        "operator": (
            """
TAKYON_OPERATOR_DATABASE_URL=postgres://operator-secret
TAKYON_APP_DATABASE_URL=postgres://app-secret
TAKYON_MIGRATION_DATABASE_URL=postgres://migration-secret
TAKYON_SAFEBOX_TOKEN=transport-secret
TAKYON_SAFEBOX_OPERATOR_TOKEN=operator-token-secret
""",
            "TAKYON_APP_DATABASE_URL",
            "app-secret",
        ),
        "subuser": (
            """
TAKYON_APP_DATABASE_URL=postgres://app-secret
TAKYON_OPERATOR_DATABASE_URL=postgres://operator-secret
TAKYON_MIGRATION_DATABASE_URL=postgres://migration-secret
TAKYON_SAFEBOX_TOKEN=transport-secret
""",
            "TAKYON_OPERATOR_DATABASE_URL",
            "operator-secret",
        ),
        "safebox": (
            """
TAKYON_SAFEBOX_DATABASE_URL=postgres://safebox-secret
TAKYON_OPERATOR_DATABASE_URL=postgres://operator-secret
TAKYON_SAFEBOX_TOKEN=transport-secret
TAKYON_SAFEBOX_OPERATOR_TOKEN=operator-token-secret
""",
            "TAKYON_OPERATOR_DATABASE_URL",
            "operator-secret",
        ),
    }
    for plane, (env_text, rejected_key, secret_value) in cases.items():
        env_file = tmp_path / f"{plane}.env"
        env_file.write_text(env_text.strip() + "\n")
        result = subprocess.run(
            ["bash", str(script), plane, str(env_file)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        combined = result.stdout + result.stderr
        assert result.returncode != 0, plane
        assert rejected_key in combined, plane
        assert secret_value not in combined, plane


def test_authority_env_validator_rejects_legacy_database_url_aliases(tmp_path):
    script = ROOT / "deploy/shared/validate-authority-env.sh"
    env_file = tmp_path / "operator.env"
    env_file.write_text(
        "\n".join(
            [
                "TAKYON_OPERATOR_DATABASE_URL=postgres://operator-secret",
                "TAKYON_MIGRATION_DATABASE_URL=postgres://migration-secret",
                "TAKYON_SAFEBOX_TOKEN=transport-secret",
                "TAKYON_SAFEBOX_OPERATOR_TOKEN=operator-token-secret",
                "DATABASE_URL=postgres://legacy-secret",
            ]
        )
        + "\n"
    )

    result = subprocess.run(
        ["bash", str(script), "operator", str(env_file)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "DATABASE_URL" in combined
    assert "legacy-secret" not in combined


def test_non_safebox_envs_reject_stripe_authority_secrets(tmp_path):
    script = ROOT / "deploy/shared/validate-authority-env.sh"
    required = {
        "operator": [
            "TAKYON_OPERATOR_DATABASE_URL=postgres://operator",
            "TAKYON_SAFEBOX_TOKEN=transport",
            "TAKYON_SAFEBOX_OPERATOR_TOKEN=operator-token",
        ],
        "subuser": [
            "TAKYON_APP_DATABASE_URL=postgres://app",
            "TAKYON_MIGRATION_DATABASE_URL=postgres://migration",
            "TAKYON_SAFEBOX_TOKEN=transport",
        ],
    }
    for plane, base in required.items():
        for key in (
            "STRIPE_SECRET_KEY",
            "STRIPE_SANDBOX_SECRET_KEY",
            "STRIPE_WEBHOOK_SECRET",
            "STRIPE_BILLING_WEBHOOK_SECRET",
            "TAKYON_MANAGED_SECRET_COMMAND",
            "TAKYON_MANAGED_SECRET_KEYS",
            "DOPPLER_TOKEN",
        ):
            env_file = tmp_path / f"{plane}-{key}.env"
            env_file.write_text("\n".join([*base, f"{key}=must-not-leak"]) + "\n")
            result = subprocess.run(
                ["bash", str(script), plane, str(env_file)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                env={"PATH": "/usr/bin:/bin"},
            )
            combined = result.stdout + result.stderr
            assert result.returncode != 0
            assert key in combined
            assert "must-not-leak" not in combined


def test_authority_env_validator_rejects_export_prefixed_forbidden_keys(tmp_path):
    script = ROOT / "deploy/shared/validate-authority-env.sh"
    cases = {
        "operator": (
            [
                "TAKYON_OPERATOR_DATABASE_URL=postgres://operator",
                "TAKYON_SAFEBOX_TOKEN=transport",
                "TAKYON_SAFEBOX_OPERATOR_TOKEN=operator-token",
                "export STRIPE_SECRET_KEY=must-not-leak",
            ],
            "STRIPE_SECRET_KEY",
        ),
        "subuser": (
            [
                "TAKYON_APP_DATABASE_URL=postgres://app",
                "TAKYON_MIGRATION_DATABASE_URL=postgres://migration",
                "TAKYON_SAFEBOX_TOKEN=transport",
                "export TAKYON_OPERATOR_DATABASE_URL=must-not-leak",
            ],
            "TAKYON_OPERATOR_DATABASE_URL",
        ),
    }
    for plane, (lines, rejected_key) in cases.items():
        env_file = tmp_path / f"{plane}-export.env"
        env_file.write_text("\n".join(lines) + "\n")
        result = subprocess.run(
            ["bash", str(script), plane, str(env_file)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
        combined = result.stdout + result.stderr
        assert result.returncode != 0
        assert rejected_key in combined
        assert "must-not-leak" not in combined


def test_authority_env_validator_reports_all_errors_without_values(tmp_path):
    script = ROOT / "deploy/shared/validate-authority-env.sh"
    env_file = tmp_path / "subuser.env"
    env_file.write_text(
        "\n".join(
            [
                "DATABASE_URL=postgres://legacy-secret",
                "TAKYON_OPERATOR_DATABASE_URL=postgres://operator-secret",
                "TAKYON_SAFEBOX_OPERATOR_TOKEN=operator-token-secret",
            ]
        )
        + "\n"
    )

    result = subprocess.run(
        ["bash", str(script), "subuser", str(env_file)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "forbidden authority env present on subuser host: DATABASE_URL" in combined
    assert "missing required authority env: TAKYON_APP_DATABASE_URL" in combined
    assert "missing required authority env: TAKYON_MIGRATION_DATABASE_URL" in combined
    assert "missing required authority env: TAKYON_SAFEBOX_TOKEN" in combined
    assert "forbidden authority env present on subuser host: TAKYON_OPERATOR_DATABASE_URL" in combined
    assert "forbidden authority env present on subuser host: TAKYON_SAFEBOX_OPERATOR_TOKEN" in combined
    for secret in ("legacy-secret", "operator-secret", "operator-token-secret"):
        assert secret not in combined
