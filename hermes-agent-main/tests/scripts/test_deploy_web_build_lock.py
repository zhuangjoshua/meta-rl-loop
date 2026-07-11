from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[3]
BUILD_HELPER = ROOT / "deploy/shared/build-web-locked.sh"


def test_web_build_helper_serializes_shared_checkout(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "package-lock.json").write_text("{}\n")

    state = tmp_path / "state"
    state.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_npm = fake_bin / "npm"
    fake_npm.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "if ! mkdir \"$BUILD_TEST_STATE/running\" 2>/dev/null; then\n"
        "  touch \"$BUILD_TEST_STATE/overlap\"\n"
        "fi\n"
        "printf '%s\\n' \"$*\" >> \"$BUILD_TEST_STATE/calls\"\n"
        "sleep 0.15\n"
        "rmdir \"$BUILD_TEST_STATE/running\" 2>/dev/null || true\n"
    )
    fake_npm.chmod(fake_npm.stat().st_mode | stat.S_IXUSR)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "BUILD_TEST_STATE": str(state),
        "TMPDIR": str(tmp_path),
    }
    processes = [
        subprocess.Popen(
            ["bash", str(BUILD_HELPER), str(web)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=10) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], results
    assert not (state / "overlap").exists()
    assert (state / "calls").read_text().splitlines() == ["ci", "run build", "ci", "run build"]


def test_all_runtime_deploys_use_shared_web_build_lock():
    for relative_path in (
        "deploy/argon-alpha-14/deploy-runtime.sh",
        "deploy/takyon-subuser/deploy-runtime.sh",
        "deploy/takyon-safebox/deploy-runtime.sh",
    ):
        source = (ROOT / relative_path).read_text()
        assert 'bash "$WEB_BUILD_SCRIPT" "$RUNTIME_DIR/web"' in source
        assert "npm ci && npm run build" not in source
