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
        },
        "deploy/argon-alpha-14/takyon-worker.service": {
            "TAKYON_APP_DATABASE_URL",
            "TAKYON_SAFEBOX_DATABASE_URL",
            "TAKYON_MIGRATION_DATABASE_URL",
            "MIGRATION_DATABASE_URL",
        },
        "deploy/argon-alpha-14/takyon-docker-broker.service": {
            "TAKYON_OPERATOR_DATABASE_URL",
            "TAKYON_APP_DATABASE_URL",
            "TAKYON_SAFEBOX_DATABASE_URL",
            "TAKYON_MIGRATION_DATABASE_URL",
            "MIGRATION_DATABASE_URL",
        },
        "deploy/takyon-subuser/takyon-subuser.service": {
            "TAKYON_OPERATOR_DATABASE_URL",
            "TAKYON_SAFEBOX_DATABASE_URL",
            "TAKYON_MIGRATION_DATABASE_URL",
            "MIGRATION_DATABASE_URL",
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
        },
    }
    for unit_path, required_unsets in expectations.items():
        names = _unset_environment_names(unit_path)
        assert required_unsets <= names, unit_path


def test_authority_env_validator_is_host_specific_and_secret_safe():
    src = (ROOT / "deploy/shared/validate-authority-env.sh").read_text()

    assert "Usage:" in src
    assert "never prints secret" in src
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
    assert "TAKYON_MIGRATION_DATABASE_URL or MIGRATION_DATABASE_URL" in src


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


def test_authority_env_validator_rejects_cross_plane_database_urls(tmp_path):
    script = ROOT / "deploy/shared/validate-authority-env.sh"
    cases = {
        "operator": (
            """
TAKYON_OPERATOR_DATABASE_URL=postgres://operator-secret
TAKYON_APP_DATABASE_URL=postgres://app-secret
MIGRATION_DATABASE_URL=postgres://migration-secret
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
MIGRATION_DATABASE_URL=postgres://migration-secret
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
