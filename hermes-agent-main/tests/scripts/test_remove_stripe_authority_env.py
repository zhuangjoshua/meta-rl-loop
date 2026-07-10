from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy/shared/remove-stripe-authority-env.py"
SPEC = importlib.util.spec_from_file_location("remove_stripe_authority_env", SCRIPT)
assert SPEC and SPEC.loader
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)


def _run(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(SCRIPT), *(str(path) for path in paths)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cleanup_removes_only_exact_stripe_authority_assignments(tmp_path):
    secret_values = ("sk_live_private", "sk_test_private", "whsec_one", "whsec_two")
    env_file = tmp_path / "runtime.env"
    env_file.write_text(
        "# STRIPE_SECRET_KEY=commented\n"
        "STRIPE_SECRET_KEY=sk_live_private\n"
        " export STRIPE_SANDBOX_SECRET_KEY =sk_test_private\n"
        "STRIPE_WEBHOOK_SECRET=whsec_one\n"
        "STRIPE_BILLING_WEBHOOK_SECRET = whsec_two\n"
        "NOT_STRIPE_SECRET_KEY=preserved\n"
        "STRIPE_SECRET_KEY_SUFFIX=preserved\n"
        "TAKYON_SAFEBOX_TOKEN=transport\n"
    )
    env_file.chmod(0o640)
    before = env_file.stat()

    result = _run(env_file)

    assert result.returncode == 0, result.stderr
    contents = env_file.read_text()
    assert contents == (
        "# STRIPE_SECRET_KEY=commented\n"
        "NOT_STRIPE_SECRET_KEY=preserved\n"
        "STRIPE_SECRET_KEY_SUFFIX=preserved\n"
        "TAKYON_SAFEBOX_TOKEN=transport\n"
    )
    after = env_file.stat()
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
    assert stat.S_IMODE(after.st_mode) == 0o640
    combined = result.stdout + result.stderr
    for secret in secret_values:
        assert secret not in combined


def test_cleanup_is_idempotent_and_does_not_rewrite_clean_files(tmp_path):
    env_file = tmp_path / "runtime.env"
    env_file.write_text("STRIPE_SECRET_KEY=never-print\nSAFE=value\n")
    first = _run(env_file)
    first_inode = env_file.stat().st_ino
    second = _run(env_file)

    assert first.returncode == second.returncode == 0
    assert "files changed: 1" in first.stdout
    assert "files changed: 0" in second.stdout
    assert env_file.stat().st_ino == first_inode
    assert "never-print" not in first.stdout + first.stderr + second.stdout + second.stderr


@pytest.mark.parametrize("symlink_component", ["file", "directory"])
def test_cleanup_rejects_symlink_targets_and_parents(tmp_path, symlink_component):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    target = real_dir / "runtime.env"
    target.write_text("STRIPE_SECRET_KEY=never-print\n")
    if symlink_component == "file":
        path = tmp_path / "runtime.env"
        path.symlink_to(target)
    else:
        linked_dir = tmp_path / "linked"
        linked_dir.symlink_to(real_dir, target_is_directory=True)
        path = linked_dir / "runtime.env"

    result = _run(path)

    assert result.returncode != 0
    assert target.read_text() == "STRIPE_SECRET_KEY=never-print\n"
    assert "never-print" not in result.stdout + result.stderr


def test_cleanup_rejects_target_identity_change_before_replace(tmp_path, monkeypatch):
    env_file = tmp_path / "runtime.env"
    env_file.write_text("STRIPE_SECRET_KEY=never-print\n")
    real_stat = cleanup.os.stat
    calls = 0

    def changed_stat(*args, **kwargs):
        nonlocal calls
        info = real_stat(*args, **kwargs)
        calls += 1
        if calls == 1:
            values = list(info)
            values[1] += 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr(cleanup.os, "stat", changed_stat)
    with pytest.raises(cleanup.UnsafeEnvPath, match="changed before atomic replacement"):
        cleanup.clean_env_file(str(env_file))
    assert env_file.read_text() == "STRIPE_SECRET_KEY=never-print\n"


def test_cleanup_skips_missing_files_without_creating_them(tmp_path):
    missing = tmp_path / "missing.env"
    result = _run(missing)
    assert result.returncode == 0
    assert "files changed: 0" in result.stdout
    assert not missing.exists()


def test_only_operator_and_subuser_deploys_run_cleanup_before_validation():
    operator = (ROOT / "deploy/argon-alpha-14/deploy-runtime.sh").read_text()
    subuser = (ROOT / "deploy/takyon-subuser/deploy-runtime.sh").read_text()
    safebox = (ROOT / "deploy/takyon-safebox/deploy-runtime.sh").read_text()

    for source in (operator, subuser):
        assert "REMOVE_STRIPE_AUTHORITY_ENV_SCRIPT" in source
        cleanup_index = source.index('< "$REMOVE_STRIPE_AUTHORITY_ENV_SCRIPT"')
        validator_index = source.index('< "$VALIDATE_AUTHORITY_ENV_SCRIPT"')
        assert cleanup_index < validator_index
    assert "REMOVE_STRIPE_AUTHORITY_ENV_SCRIPT" not in safebox
