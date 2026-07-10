from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[3]
_HELPER = _ROOT / "deploy" / "argon-alpha-14" / "provision-operator-access-db.py"
_SPEC = importlib.util.spec_from_file_location("_operator_access_provisioner", _HELPER)
assert _SPEC and _SPEC.loader
provisioner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(provisioner)

_ISOLATOR_HELPER = (
    _ROOT / "deploy" / "argon-alpha-14" / "isolate-operator-migration-dsn.py"
)
_ISOLATOR_SPEC = importlib.util.spec_from_file_location(
    "_operator_migration_isolator", _ISOLATOR_HELPER
)
assert _ISOLATOR_SPEC and _ISOLATOR_SPEC.loader
isolator = importlib.util.module_from_spec(_ISOLATOR_SPEC)
_ISOLATOR_SPEC.loader.exec_module(isolator)


def test_provisioner_preserves_supabase_pooler_suffix_without_leaking_old_login():
    dsn = provisioner._scoped_login_dsn(
        "postgresql://takyon_migration.projectref:old@pooler.example:5432/postgres?sslmode=require",
        "new-secret",
    )
    assert dsn == (
        "postgresql://takyon_operator_access.projectref:new-secret@"
        "pooler.example:5432/postgres?sslmode=require"
    )
    assert "takyon_migration" not in dsn
    assert ":old@" not in dsn


def test_provisioner_sends_only_scram_verifier_to_database():
    password = "plain-secret-that-must-not-enter-sql"
    verifier = provisioner._scram_verifier(password, salt=b"0123456789abcdef")
    assert verifier.startswith("SCRAM-SHA-256$4096:")
    assert password not in verifier
    assert verifier == provisioner._scram_verifier(password, salt=b"0123456789abcdef")


def test_root_credential_write_is_atomic_and_mode_0600(tmp_path, monkeypatch):
    directory = tmp_path / "operator-access"
    path = directory / "database-url"
    monkeypatch.setattr(os, "chown", lambda *_args: None)
    monkeypatch.setattr(os, "fchown", lambda *_args: None)
    provisioner._write_credential(
        "postgresql://takyon_operator_access:secret@example/db",
        directory=directory,
        path=path,
    )
    assert path.read_text().strip().startswith("postgresql://takyon_operator_access:")
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(directory.glob(".database-url.*"))


def test_provisioning_rail_sanitizes_remote_environment_and_never_passes_secret():
    shell = (
        _ROOT / "deploy" / "argon-alpha-14" / "provision-operator-access-db.sh"
    ).read_text()
    deploy = (_ROOT / "deploy" / "argon-alpha-14" / "deploy-runtime.sh").read_text()
    bootstrap = (_ROOT / "deploy" / "argon-alpha-14" / "bootstrap-host.sh").read_text()
    assert "exec env -i" in shell
    assert "TAKYON_ENV=prod" in shell
    assert "TAKYON_HOST_ROLE=operator" in shell
    assert "password=" not in shell.lower()
    assert "provision-operator-access-db.sh" in deploy
    assert "/root/.config/takyon/operator-access" in bootstrap
    assert "/root/.config/takyon/migration" in bootstrap
    assert "resolve_database_url" not in (_HELPER.read_text())


def test_migration_dsn_is_moved_root_only_and_removed_from_runtime_files(
    tmp_path, monkeypatch
):
    root_dir = tmp_path / "root" / "migration"
    root_file = root_dir / "database-url"
    runtime = tmp_path / "runtime.env"
    secrets = tmp_path / "secrets.env"
    dsn = "postgresql://takyon_migration.ref:secret@pooler/db"
    runtime.write_text(f"KEEP=1\nTAKYON_MIGRATION_DATABASE_URL={dsn}\n")
    secrets.write_text(f"MIGRATION_DATABASE_URL={dsn}\nSAFE=1\n")
    runtime.chmod(0o600)
    secrets.chmod(0o600)
    monkeypatch.setattr(isolator, "ROOT_DIR", root_dir)
    monkeypatch.setattr(isolator, "ROOT_FILE", root_file)
    monkeypatch.setattr(isolator, "RUNTIME_ENV_FILES", (runtime, secrets))
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda *_args: None)
    monkeypatch.setattr(os, "fchown", lambda *_args: None)
    monkeypatch.setattr(
        isolator,
        "_replacement_metadata_matches",
        lambda *_args, **_kwargs: True,
    )

    assert isolator.isolate() == "isolated"
    assert root_file.read_text().strip() == dsn
    assert root_file.stat().st_mode & 0o777 == 0o600
    assert "MIGRATION_DATABASE_URL" not in runtime.read_text()
    assert "MIGRATION_DATABASE_URL" not in secrets.read_text()
    assert runtime.read_text() == "KEEP=1\n"
    assert secrets.read_text() == "SAFE=1\n"


def test_operator_deploy_isolates_before_validation_and_runs_migrations_as_root():
    deploy = (_ROOT / "deploy" / "argon-alpha-14" / "deploy-runtime.sh").read_text()
    isolate_call = deploy.index('"$ISOLATE_OPERATOR_MIGRATION_DSN_SCRIPT"')
    validate_call = deploy.index('< "$VALIDATE_AUTHORITY_ENV_SCRIPT"')
    assert isolate_call < validate_call
    migration_body = deploy.split("run_remote_migrations()", 1)[1].split(
        "wait_for_remote_runtime_idle()", 1
    )[0]
    assert "migration_dir=/root/.config/takyon/migration" in migration_body
    assert 'migration_file="$migration_dir/database-url"' in migration_body
    assert "runuser -u takyon" not in migration_body
    assert "env -i" in migration_body


def test_operator_service_env_rejects_broad_migration_dsn(tmp_path):
    validator = _ROOT / "deploy/shared/validate-authority-env.sh"
    safe = tmp_path / "safe.env"
    safe.write_text(
        "TAKYON_OPERATOR_DATABASE_URL=postgres://operator\n"
        "TAKYON_SAFEBOX_TOKEN=transport\n"
        "TAKYON_SAFEBOX_OPERATOR_TOKEN=operator-token\n"
    )
    clean_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    accepted = subprocess.run(
        ["bash", str(validator), "operator", str(safe)],
        text=True,
        capture_output=True,
        check=False,
        env=clean_env,
    )
    assert accepted.returncode == 0, accepted.stderr

    safe.write_text(
        safe.read_text() + "TAKYON_MIGRATION_DATABASE_URL=postgres://broad-secret\n"
    )
    rejected = subprocess.run(
        ["bash", str(validator), "operator", str(safe)],
        text=True,
        capture_output=True,
        check=False,
        env=clean_env,
    )
    combined = rejected.stdout + rejected.stderr
    assert rejected.returncode != 0
    assert "TAKYON_MIGRATION_DATABASE_URL" in combined
    assert "broad-secret" not in combined


def test_provisioner_rejects_non_root_or_non_prod_operator(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 995)
    with pytest.raises(provisioner.ProvisionError, match="root"):
        provisioner._assert_root()

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setenv("TAKYON_ENV", "dev")
    monkeypatch.setenv("TAKYON_HOST_ROLE", "operator")
    with pytest.raises(provisioner.ProvisionError, match="production operator"):
        provisioner._assert_root()
