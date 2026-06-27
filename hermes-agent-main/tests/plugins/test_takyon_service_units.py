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


def test_authority_env_validator_is_host_specific_and_secret_safe():
    src = (ROOT / "deploy/shared/validate-authority-env.sh").read_text()

    assert "Usage:" in src
    assert "never prints secret" in src
    assert "operator)" in src
    assert "require_key TAKYON_OPERATOR_DATABASE_URL" in src
    assert "require_key TAKYON_SAFEBOX_OPERATOR_TOKEN" in src
    assert "subuser)" in src
    assert "require_key TAKYON_APP_DATABASE_URL" in src
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
