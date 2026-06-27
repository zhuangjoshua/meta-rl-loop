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
