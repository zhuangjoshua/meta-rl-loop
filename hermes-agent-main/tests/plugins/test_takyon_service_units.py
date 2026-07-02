from pathlib import Path
import subprocess


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
        assert preflight_index < src.index("rsync -az --delete --force"), plane
        assert preflight_index < src.index("systemctl restart"), plane


def test_operator_deploy_can_skip_runtime_migrations_after_manual_apply():
    src = (ROOT / "deploy/argon-alpha-14/deploy-runtime.sh").read_text()

    assert 'TAKYON_RUN_DB_MIGRATIONS="${TAKYON_RUN_DB_MIGRATIONS:-1}"' in src
    assert "if [[ '$TAKYON_RUN_DB_MIGRATIONS' == '1' ]] && grep -F -- 'TAKYON_DB_BACKEND=postgres'" in src


def test_operator_deploy_drain_probe_survives_ssh_double_quoted_string():
    src = (ROOT / "deploy/argon-alpha-14/deploy-runtime.sh").read_text()

    drain = src.split("wait_for_remote_runtime_idle() {", 1)[1].split("wait_for_remote_runtime_idle", 1)[0]
    assert 'resolve_database_url(plane="operator")' not in drain
    assert 'assert_takyon_pg_role(conn, "operator")' not in drain
    assert "resolve_database_url(plane='operator')" in drain
    assert "assert_takyon_pg_role(conn, 'operator')" in drain


def test_operator_prod_script_targets_the_exact_active_local_worker_pool():
    src = (ROOT / "scripts/takyon-operator-prod.sh").read_text()

    load = src.split("load_operator_env() {", 1)[1].split("unset_raw_runtime_authority_env() {", 1)[0]
    worker = src.split("cmd_worker() {", 1)[1].split("cmd_worker_once() {", 1)[0]
    console = src.split("cmd_console() {", 1)[1].split("cmd_vps_worker() {", 1)[0]

    assert 'ACTIVE_LOCAL_WORKER_PREFIX_FILE=' in src
    assert "local_worker_prefix_for_pid() {" in src
    assert "record_active_local_worker_prefix() {" in src
    assert "active_local_worker_prefix() {" in src
    assert "resolve_preferred_worker_id_prefix() {" in src
    assert 'export TAKYON_PREFERRED_WORKER_ID_PREFIX="${TAKYON_PREFERRED_WORKER_ID_PREFIX:-mac-operator-$(hostname -s)-}"' not in load
    assert 'preferred_worker_prefix="$(resolve_preferred_worker_id_prefix)"' in load
    assert 'export TAKYON_PREFERRED_WORKER_ID_PREFIX="$preferred_worker_prefix"' in load
    assert 'worker_prefix="$(record_active_local_worker_prefix "$$" || true)"' in worker
    assert '--worker-id "$worker_id"' in worker
    assert 'worker_prefix="$(record_active_local_worker_prefix "$worker_pid" || true)"' in console
    assert 'export TAKYON_PREFERRED_WORKER_ID_PREFIX="$worker_prefix"' in console


def test_operator_prod_script_handles_empty_local_worker_pool_under_nounset():
    src = (ROOT / "scripts/takyon-operator-prod.sh").read_text()

    collect = src.split("collect_local_worker_pids() {", 1)[1].split("_wait_for_local_worker_exit() {", 1)[0]
    assert 'if [[ "${#pids[@]}" -eq 0 ]]; then' in collect
    assert "return 0" in collect
    assert "printf '%s\\n' \"${pids[@]}\"" in collect


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
