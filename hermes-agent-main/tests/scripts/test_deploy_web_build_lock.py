from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[3]
BUILD_HELPER = ROOT / "deploy/shared/build-web-locked.sh"
_ACTIVE_PROCESSES: set[subprocess.Popen[str]] = set()


def _run(*args: str | Path, cwd: Path) -> str:
    result = subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Deploy Lock Test",
            "GIT_AUTHOR_EMAIL": "deploy-lock@test.invalid",
            "GIT_COMMITTER_NAME": "Deploy Lock Test",
            "GIT_COMMITTER_EMAIL": "deploy-lock@test.invalid",
        },
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _make_revision_worktrees(tmp_path: Path) -> tuple[Path, Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run("git", "init", "--quiet", "-b", "main", cwd=repo)

    runtime = repo / "hermes-agent-main"
    (runtime / "web").mkdir(parents=True)
    (runtime / "takyon_cli").mkdir()
    (runtime / "plugins" / "takyon").mkdir(parents=True)
    (runtime / "tui_gateway").mkdir()
    (runtime / "web" / "package-lock.json").write_text("{}\n")
    (runtime / "revision.txt").write_text("revision-one\n")
    (runtime / "plugins" / "takyon" / "marker.py").write_text("REVISION = 'one'\n")
    deploy = repo / "deploy"
    deploy.mkdir()
    (deploy / "promotion-revision.txt").write_text("revision-one\n")
    _write_executable(
        deploy / "promote-artifact",
        "#!/bin/sh\n"
        "set -eu\n"
        "case \"$0\" in \"$TAKYON_DEPLOY_REPO_ARTIFACT\"/*) ;; *) exit 41 ;; esac\n"
        "revision=$(cat \"$(dirname \"$0\")/promotion-revision.txt\")\n"
        "runtime_revision=$(cat \"$TAKYON_DEPLOY_RUNTIME_ARTIFACT/revision.txt\")\n"
        "test \"$revision\" = \"$runtime_revision\"\n"
        "test ! -w \"$(dirname \"$0\")/promotion-revision.txt\"\n"
        "printf '%s|%s\n' \"$revision\" \"$0\" > \"$RESULT_PATH\"\n",
    )
    _run("git", "add", ".", cwd=repo)
    _run("git", "commit", "--quiet", "-m", "revision one", cwd=repo)

    (runtime / "revision.txt").write_text("revision-two\n")
    (runtime / "plugins" / "takyon" / "marker.py").write_text("REVISION = 'two'\n")
    (deploy / "promotion-revision.txt").write_text("revision-two\n")
    _run("git", "commit", "--quiet", "-am", "revision two", cwd=repo)
    revision_one, revision_two = _run(
        "git", "rev-parse", "HEAD^", "HEAD", cwd=repo
    ).splitlines()

    worktree_one = tmp_path / "worktree-one"
    _run(
        "git",
        "worktree",
        "add",
        "--quiet",
        "-b",
        "revision-one",
        str(worktree_one),
        revision_one,
        cwd=repo,
    )
    return (
        worktree_one / "hermes-agent-main",
        runtime,
        revision_one,
        revision_two,
    )


def _make_fake_commands(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_npm = fake_bin / "npm"
    _write_executable(
        fake_npm,
        "#!/bin/sh\n"
        "set -eu\n"
        "revision=$(cat ../revision.txt)\n"
        "if ! mkdir \"$BUILD_TEST_STATE/running\" 2>/dev/null; then\n"
        "  touch \"$BUILD_TEST_STATE/overlap\"\n"
        "fi\n"
        "cleanup() { rmdir \"$BUILD_TEST_STATE/running\" 2>/dev/null || true; }\n"
        "trap cleanup EXIT INT TERM\n"
        "printf '%s %s\\n' \"$revision\" \"$*\" >> \"$BUILD_TEST_STATE/calls\"\n"
        "if [ \"${SPAWN_BUILD_DESCENDANT:-0}\" = '1' ] && [ \"$*\" = 'ci' ]; then\n"
        "  (trap '' INT TERM; sleep 300) &\n"
        "  printf '%s\\n' \"$!\" > \"$BUILD_TEST_STATE/build-descendant-pid\"\n"
        "  touch \"$BUILD_TEST_STATE/build-started\"\n"
        "fi\n"
        "if [ \"${BLOCK_BUILD:-0}\" = '1' ] && [ \"$*\" = 'ci' ]; then\n"
        "  while [ ! -f \"$BUILD_TEST_STATE/release-build\" ]; do sleep 0.02; done\n"
        "fi\n"
        "sleep \"${BUILD_TEST_SLEEP:-0.05}\"\n"
        "if [ \"$*\" = 'run build' ]; then\n"
        "  mkdir -p ../takyon_cli/web_dist/litebulb\n"
        "  printf '%s\\n' \"$revision\" > ../takyon_cli/web_dist/litebulb/bundle.js\n"
        "fi\n",
    )

    consumer = fake_bin / "consume-artifact"
    _write_executable(
        consumer,
        "#!/bin/sh\n"
        "set -eu\n"
        "artifact=$TAKYON_DEPLOY_RUNTIME_ARTIFACT\n"
        "revision=$(cat \"$artifact/revision.txt\")\n"
        "bundle=$(cat \"$artifact/takyon_cli/web_dist/litebulb/bundle.js\")\n"
        "test \"$revision\" = \"$bundle\"\n"
        "test ! -w \"$artifact/revision.txt\"\n"
        "test -s \"$artifact/.takyon-deploy-artifact.json\"\n"
        "cp \"$artifact/.takyon-deploy-artifact.json\" \"$RESULT_PATH.manifest\"\n"
        "printf '%s|%s|%s|%s\\n' \"$revision\" \"$bundle\" \"$artifact\" \"$TAKYON_DEPLOY_SOURCE_REVISION\" > \"$RESULT_PATH\"\n"
        "if [ \"${SPAWN_PROMOTION_DESCENDANT:-0}\" = '1' ]; then\n"
        "  if [ \"${IGNORE_PROMOTION_SIGNAL:-0}\" = '1' ]; then\n"
        "    (trap '' INT TERM; sleep 300) &\n"
        "  else\n"
        "    sleep 300 &\n"
        "  fi\n"
        "  printf '%s\\n' \"$!\" > \"$BUILD_TEST_STATE/promotion-descendant-pid\"\n"
        "fi\n"
        "if [ \"${BLOCK_PROMOTION:-0}\" = '1' ]; then\n"
        "  touch \"$BUILD_TEST_STATE/promotion-started-$revision\"\n"
        "  while [ ! -f \"$BUILD_TEST_STATE/release-promotion\" ]; do sleep 0.02; done\n"
        "fi\n",
    )
    return fake_bin, consumer


def _deploy_process(
    runtime: Path,
    consumer: Path,
    *,
    fake_bin: Path,
    state: Path,
    home: Path,
    tmpdir: Path,
    result_path: Path,
    block_promotion: bool = False,
    spawn_promotion_descendant: bool = False,
    ignore_promotion_signal: bool = False,
    block_build: bool = False,
    spawn_build_descendant: bool = False,
    target_ref: str | None = None,
    waiting_marker: Path | None = None,
) -> subprocess.Popen[str]:
    tmpdir.mkdir(exist_ok=True)
    if target_ref is None:
        branch = _run("git", "branch", "--show-current", cwd=runtime.parent)
        target_ref = f"refs/heads/{branch}"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "BUILD_TEST_STATE": str(state),
        "HOME": str(home),
        "TMPDIR": str(tmpdir),
        "RESULT_PATH": str(result_path),
        "BUILD_TEST_SLEEP": "0.12",
        "BLOCK_PROMOTION": "1" if block_promotion else "0",
        "SPAWN_PROMOTION_DESCENDANT": "1" if spawn_promotion_descendant else "0",
        "IGNORE_PROMOTION_SIGNAL": "1" if ignore_promotion_signal else "0",
        "BLOCK_BUILD": "1" if block_build else "0",
        "SPAWN_BUILD_DESCENDANT": "1" if spawn_build_descendant else "0",
        "TAKYON_DEPLOY_TARGET_REF": target_ref,
        "TAKYON_DEPLOY_INTERRUPT_GRACE_SECONDS": "0.2",
    }
    if waiting_marker is not None:
        env["TAKYON_DEPLOY_WAITING_MARKER"] = str(waiting_marker)
    process = subprocess.Popen(
        ["bash", str(BUILD_HELPER), str(runtime), "--", str(consumer)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _ACTIVE_PROCESSES.add(process)
    return process


@pytest.fixture(autouse=True)
def _reap_deploy_processes_after_failure():
    """A timed-out assertion must not leave a blocked fake deploy mutating later tests."""
    yield
    for process in tuple(_ACTIVE_PROCESSES):
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)
        else:
            process.communicate()
    _ACTIVE_PROCESSES.clear()


def _wait_for(path: Path, *, process: subprocess.Popen[str] | None = None) -> None:
    # The suite-wide 30-second alarm is the single timeout authority. Nested 10-second marker
    # deadlines made a merely CPU-starved xdist worker abandon a live helper and leak its blocked
    # children into later tests.
    while not path.exists():
        if process is not None and process.poll() is not None:
            output = process.communicate()
            raise AssertionError(
                f"deploy exited before creating {path}: returncode={process.returncode}, "
                f"output={output!r}"
            )
        time.sleep(0.02)


def _artifact_path(result_path: Path) -> Path:
    return Path(result_path.read_text().strip().split("|")[2])


def _assert_artifact_manifest(result_path: Path, revision: str) -> None:
    manifest = json.loads(Path(f"{result_path}.manifest").read_text())
    assert manifest["source_revision"] == revision
    assert len(manifest["repository_tree"]) == 40
    assert manifest["runtime_path"] == "hermes-agent-main"
    assert len(manifest["runtime_tree"]) == 40
    assert len(manifest["web_dist_sha256"]) == 64


def _wait_for_pid_exit(pid: int) -> None:
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)


def test_concurrent_worktree_deploys_consume_revision_consistent_artifacts(tmp_path):
    runtime_one, runtime_two, revision_one, revision_two = _make_revision_worktrees(tmp_path)
    fake_bin, consumer = _make_fake_commands(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    home = tmp_path / "shared-home"
    home.mkdir()
    result_one = tmp_path / "result-one"
    result_two = tmp_path / "result-two"

    processes = [
        _deploy_process(
            runtime_one,
            consumer,
            fake_bin=fake_bin,
            state=state,
            home=home,
            tmpdir=tmp_path / "tmp-one",
            result_path=result_one,
        ),
        _deploy_process(
            runtime_two,
            consumer,
            fake_bin=fake_bin,
            state=state,
            home=home,
            tmpdir=tmp_path / "tmp-two",
            result_path=result_two,
        ),
    ]
    results = [process.communicate() for process in processes]

    assert [process.returncode for process in processes] == [0, 0], results
    assert not (state / "overlap").exists()
    assert result_one.read_text().split("|", 2)[:2] == ["revision-one", "revision-one"]
    assert result_two.read_text().split("|", 2)[:2] == ["revision-two", "revision-two"]
    _assert_artifact_manifest(result_one, revision_one)
    _assert_artifact_manifest(result_two, revision_two)
    assert not _artifact_path(result_one).exists()
    assert not _artifact_path(result_two).exists()

    calls = (state / "calls").read_text().splitlines()
    assert calls in (
        [
            "revision-one ci",
            "revision-one run build",
            "revision-two ci",
            "revision-two run build",
        ],
        [
            "revision-two ci",
            "revision-two run build",
            "revision-one ci",
            "revision-one run build",
        ],
    )
    lock_files = list((home / ".takyon-deploy-locks").glob("*.lock"))
    assert len(lock_files) == 1
    owner = json.loads(lock_files[0].read_text())
    assert owner["revision"] in {revision_one, revision_two}


def test_repo_owned_promotion_command_runs_from_sealed_outer_artifact(tmp_path):
    runtime_one, _runtime_two, _revision_one, _revision_two = _make_revision_worktrees(
        tmp_path
    )
    fake_bin, _consumer = _make_fake_commands(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    result = tmp_path / "result"
    promotion = runtime_one.parent / "deploy" / "promote-artifact"

    process = _deploy_process(
        runtime_one,
        promotion,
        fake_bin=fake_bin,
        state=state,
        home=home,
        tmpdir=tmp_path / "tmp",
        result_path=result,
    )
    output = process.communicate()

    assert process.returncode == 0, output
    revision, invoked_path = result.read_text().strip().split("|", 1)
    assert revision == "revision-one"
    assert invoked_path != str(promotion)
    assert not Path(invoked_path).exists()


def test_outer_repository_wins_over_nested_runtime_git_metadata(tmp_path):
    _runtime_one, runtime_two, _revision_one, revision_two = _make_revision_worktrees(
        tmp_path
    )
    _run("git", "init", "--quiet", "-b", "nested", cwd=runtime_two)
    _run("git", "add", ".", cwd=runtime_two)
    _run("git", "commit", "--quiet", "-m", "divergent nested metadata", cwd=runtime_two)
    assert _run("git", "status", "--porcelain", cwd=runtime_two.parent) == ""

    fake_bin, consumer = _make_fake_commands(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    result = tmp_path / "result"
    process = _deploy_process(
        runtime_two,
        consumer,
        fake_bin=fake_bin,
        state=state,
        home=home,
        tmpdir=tmp_path / "tmp",
        result_path=result,
    )
    output = process.communicate()

    assert process.returncode == 0, output
    assert result.read_text().strip().split("|")[3] == revision_two


def test_interrupt_terminates_promotion_process_group(tmp_path):
    runtime_one, _runtime_two, _revision_one, _revision_two = _make_revision_worktrees(
        tmp_path
    )
    fake_bin, consumer = _make_fake_commands(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    process = _deploy_process(
        runtime_one,
        consumer,
        fake_bin=fake_bin,
        state=state,
        home=home,
        tmpdir=tmp_path / "tmp",
        result_path=tmp_path / "result",
        block_promotion=True,
        spawn_promotion_descendant=True,
        ignore_promotion_signal=True,
    )
    _wait_for(state / "promotion-descendant-pid", process=process)
    _wait_for(state / "promotion-started-revision-one", process=process)
    descendant_pid = int((state / "promotion-descendant-pid").read_text())

    process.terminate()
    output = process.communicate()

    assert process.returncode == 128 + 15, output
    _wait_for_pid_exit(descendant_pid)


def test_interrupt_terminates_npm_build_process_group(tmp_path):
    runtime_one, _runtime_two, _revision_one, _revision_two = _make_revision_worktrees(
        tmp_path
    )
    fake_bin, consumer = _make_fake_commands(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    process = _deploy_process(
        runtime_one,
        consumer,
        fake_bin=fake_bin,
        state=state,
        home=home,
        tmpdir=tmp_path / "tmp",
        result_path=tmp_path / "result",
        block_build=True,
        spawn_build_descendant=True,
    )
    _wait_for(state / "build-descendant-pid", process=process)
    _wait_for(state / "build-started", process=process)
    descendant_pid = int((state / "build-descendant-pid").read_text())

    process.terminate()
    output = process.communicate()

    assert process.returncode == 128 + 15, output
    _wait_for_pid_exit(descendant_pid)


def test_interrupted_waiter_cannot_release_owners_promotion_lock(tmp_path):
    runtime_one, runtime_two, _revision_one, _revision_two = _make_revision_worktrees(tmp_path)
    fake_bin, consumer = _make_fake_commands(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    home = tmp_path / "shared-home"
    home.mkdir()

    owner_result = tmp_path / "owner-result"
    owner = _deploy_process(
        runtime_one,
        consumer,
        fake_bin=fake_bin,
        state=state,
        home=home,
        tmpdir=tmp_path / "owner-tmp",
        result_path=owner_result,
        block_promotion=True,
    )
    _wait_for(state / "promotion-started-revision-one", process=owner)
    owner_calls = (state / "calls").read_text().splitlines()
    assert owner_calls == ["revision-one ci", "revision-one run build"]

    waiter_waiting = tmp_path / "waiter-waiting"
    waiter = _deploy_process(
        runtime_two,
        consumer,
        fake_bin=fake_bin,
        state=state,
        home=home,
        tmpdir=tmp_path / "waiter-tmp",
        result_path=tmp_path / "waiter-result",
        waiting_marker=waiter_waiting,
    )
    _wait_for(waiter_waiting, process=waiter)
    waiter.terminate()
    waiter_output = waiter.communicate()
    assert waiter.returncode in {-15, 128 + 15}, waiter_output
    assert owner.poll() is None

    contender_result = tmp_path / "contender-result"
    contender_waiting = tmp_path / "contender-waiting"
    contender = _deploy_process(
        runtime_two,
        consumer,
        fake_bin=fake_bin,
        state=state,
        home=home,
        tmpdir=tmp_path / "contender-tmp",
        result_path=contender_result,
        waiting_marker=contender_waiting,
    )
    _wait_for(contender_waiting, process=contender)
    assert contender.poll() is None
    assert not contender_result.exists()
    assert (state / "calls").read_text().splitlines() == owner_calls

    (state / "release-promotion").touch()
    owner_output = owner.communicate()
    contender_output = contender.communicate()
    assert owner.returncode == 0, owner_output
    assert contender.returncode == 0, contender_output
    assert contender_result.read_text().split("|", 2)[:2] == [
        "revision-two",
        "revision-two",
    ]


def test_waiter_rejects_when_head_advances_while_waiting(tmp_path):
    runtime_one, runtime_two, _revision_one, revision_two = _make_revision_worktrees(
        tmp_path
    )
    fake_bin, consumer = _make_fake_commands(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    owner = _deploy_process(
        runtime_one,
        consumer,
        fake_bin=fake_bin,
        state=state,
        home=home,
        tmpdir=tmp_path / "owner-tmp",
        result_path=tmp_path / "owner-result",
        block_promotion=True,
    )
    _wait_for(state / "promotion-started-revision-one", process=owner)

    waiter_result = tmp_path / "waiter-result"
    waiter_waiting = tmp_path / "waiter-waiting"
    waiter = _deploy_process(
        runtime_two,
        consumer,
        fake_bin=fake_bin,
        state=state,
        home=home,
        tmpdir=tmp_path / "waiter-tmp",
        result_path=waiter_result,
        target_ref="refs/heads/main",
        waiting_marker=waiter_waiting,
    )
    _wait_for(waiter_waiting, process=waiter)
    (runtime_two / "revision.txt").write_text("revision-three\n")
    (runtime_two / "plugins" / "takyon" / "marker.py").write_text(
        "REVISION = 'three'\n"
    )
    (runtime_two.parent / "deploy" / "promotion-revision.txt").write_text(
        "revision-three\n"
    )
    _run("git", "add", ".", cwd=runtime_two.parent)
    _run("git", "commit", "-m", "revision three", cwd=runtime_two.parent)

    (state / "release-promotion").touch()
    owner_output = owner.communicate()
    waiter_output = waiter.communicate()

    assert owner.returncode == 0, owner_output
    assert waiter.returncode == 1, waiter_output
    assert "worktree HEAD changed while waiting for deploy lock" in waiter_output[1]
    assert revision_two in waiter_output[1]
    assert not waiter_result.exists()


def test_old_worktree_cannot_promote_after_published_target_advances(tmp_path):
    runtime_one, _runtime_two, revision_one, revision_two = _make_revision_worktrees(tmp_path)
    fake_bin, consumer = _make_fake_commands(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    result = tmp_path / "result"

    process = _deploy_process(
        runtime_one,
        consumer,
        fake_bin=fake_bin,
        state=state,
        home=home,
        tmpdir=tmp_path / "tmp",
        result_path=result,
        target_ref="refs/heads/main",
    )
    output = process.communicate()

    assert process.returncode == 1, output
    assert "refusing stale or unpublished promotion" in output[1]
    assert f"HEAD={revision_one}" in output[1]
    assert f"refs/heads/main={revision_two}" in output[1]
    assert not result.exists()


def test_all_runtime_deploys_hold_shared_lock_and_consume_artifact():
    deploy_paths = (
        "deploy/argon-alpha-14/deploy-runtime.sh",
        "deploy/takyon-subuser/deploy-runtime.sh",
        "deploy/takyon-safebox/deploy-runtime.sh",
    )
    for relative_path in deploy_paths:
        source = (ROOT / relative_path).read_text()
        assert (
            'exec bash "$WEB_BUILD_SCRIPT" "$RUNTIME_DIR" -- "$ROOT_DIR/'
            in source
        )
        assert 'DEPLOY_REPO_DIR="${TAKYON_DEPLOY_REPO_ARTIFACT:-}"' in source
        assert 'DEPLOY_RUNTIME_DIR="${TAKYON_DEPLOY_RUNTIME_ARTIFACT:-}"' in source
        assert '$(cd "$DEPLOY_REPO_DIR" && pwd -P)' in source
        assert '$(cd "$ROOT_DIR" && pwd -P)' in source
        assert 'takyon_stage_runtime_release \\' in source
        assert '"$DEPLOY_RUNTIME_DIR" "$TAKYON_VPS_HOST"' in source
        assert '"$RUNTIME_DIR/" \\' not in source
        assert "npm ci && npm run build" not in source

    subuser_source = (ROOT / "deploy/takyon-subuser/deploy-runtime.sh").read_text()
    assert subuser_source.index('exec bash "$WEB_BUILD_SCRIPT"') < subuser_source.index(
        'if [[ "$TAKYON_SUBUSER_FANOUT_CHILD"'
    )
    assert 'TAKYON_VPS_HOST_EXPLICIT"' in subuser_source
    assert 'TAKYON_SUBUSER_REPLICA_HOSTS="${TAKYON_SUBUSER_REPLICA_HOSTS:-root@206.81.10.173}"' in subuser_source
    assert '&& -z "$TAKYON_VPS_HOST_EXPLICIT"' not in subuser_source
    assert "TAKYON_SUBUSER_DEPLOY_PHASE=stage" in subuser_source
    assert "TAKYON_SUBUSER_DEPLOY_PHASE=activate" in subuser_source
    assert subuser_source.index("# Stage non-serving replicas first") < subuser_source.index(
        "# Activate and prove every replica"
    )
    assert "systemctl stop '$TAKYON_REMOTE_SERVICE_NAME'" in subuser_source
    assert "TAKYON_SUBUSER_DEPLOY_PHASE=rollback" in subuser_source
    assert "TAKYON_SUBUSER_DEPLOY_PHASE=finalize" in subuser_source
    assert subuser_source.index('activated_hosts+=("$host")') < subuser_source.index(
        "TAKYON_SUBUSER_DEPLOY_PHASE=activate"
    )
    assert subuser_source.index("subuser_plane_committed=1") < subuser_source.index(
        "TAKYON_SUBUSER_DEPLOY_PHASE=finalize"
    )
    assert "sub-user deploy never runs migrations" in subuser_source
    assert 'if (( ${#staged_hosts_done[@]} )); then' in subuser_source
    assert 'rsync -a --force \\"\\$incoming/.takyon/product-sites/\\"' in subuser_source

    safebox_source = (ROOT / "deploy/takyon-safebox/deploy-runtime.sh").read_text()
    assert 'TAKYON_RUN_WEB_BUILD="${TAKYON_RUN_WEB_BUILD:-1}"' in safebox_source

    workflow_source = (ROOT / ".github/workflows/deploy.yml").read_text()
    assert 'TAKYON_RUN_WEB_BUILD: "0"' not in workflow_source
    assert workflow_source.count('TAKYON_RUN_WEB_BUILD: "1"') == 3


def test_subuser_explicit_replica_never_replaces_canonical_primary(tmp_path):
    source = (ROOT / "deploy/takyon-subuser/deploy-runtime.sh").read_text()
    union_logic = source[
        source.index("host_endpoint() {") : source.index("# Stage non-serving replicas first")
    ]
    harness = tmp_path / "subuser-host-union.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        "TAKYON_SUBUSER_PRIMARY_HOST=root@134.209.123.8\n"
        "TAKYON_SUBUSER_CANONICAL_PRIMARY_HOST=root@134.209.123.8\n"
        "TAKYON_SUBUSER_CANONICAL_REPLICA_HOST=root@206.81.10.173\n"
        "TAKYON_SUBUSER_REPLICA_HOSTS=root@206.81.10.173\n"
        "TAKYON_VPS_HOST_EXPLICIT=x\n"
        "TAKYON_VPS_HOST=root@206.81.10.173\n"
        + union_logic
        + "\nprintf '%s\\n' \"${hosts[@]}\"\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)], text=True, capture_output=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "root@134.209.123.8",
        "root@206.81.10.173",
    ]


def test_subuser_remote_activation_shell_is_syntax_valid(tmp_path):
    source = (ROOT / "deploy/takyon-subuser/deploy-runtime.sh").read_text()
    activation_start = source.index("activate_subuser_host() {")
    activation = source[
        activation_start : source.index(
            '\ncase "$TAKYON_SUBUSER_DEPLOY_PHASE" in', activation_start
        )
    ]
    harness = tmp_path / "subuser-activation-parse.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        "ssh() { local last=''; for last in \"$@\"; do :; done; bash -n -c \"$last\"; }\n"
        "TAKYON_VPS_KEY=/tmp/key\nTAKYON_VPS_HOST=root@example\n"
        "TAKYON_REMOTE_SERVICE_NAME=takyon-subuser.service\n"
        "TAKYON_REMOTE_SERVICE_FILE=/etc/systemd/system/takyon-subuser.service\n"
        "TAKYON_REMOTE_HOME=/opt/takyon/.takyon\n"
        "TAKYON_DEPLOY_SOURCE_REVISION=0123456789012345678901234567890123456789\n"
        "TAKYON_SUBUSER_IS_PRIMARY=1\nTAKYON_SUBUSER_ALLOW_PRIMARY_HARD_RESTART=0\n"
        "TAKYON_HEALTH_WAIT_SECONDS=2\n"
        "TAKYON_REMOTE_RUNTIME=/opt/takyon/hermes-agent-main\n"
        "TAKYON_APPLY_CADDY=0\nROOT_DIR=/tmp\n"
        "rollback_subuser_host() { :; }\n"
        "takyon_prepare_runtime_rollback() { :; }\n"
        "takyon_begin_runtime_activation() { :; }\n"
        "takyon_activate_staged_runtime() { :; }\n"
        "remote_service_candidate=/tmp/takyon-subuser.service\n"
        "remote_service_backup=/tmp/takyon-subuser.backup.service\n"
        "remote_unit_activation_marker=/tmp/takyon-subuser.unit-installed\n"
        "remote_skills_backup=/tmp/home-skills\n"
        "remote_skills_existed_marker=/tmp/home-skills-existed\n"
        "remote_skills_activation_marker=/tmp/home-skills-installed\n"
        + activation
        + "\nactivate_subuser_host\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)], text=True, capture_output=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_bundled_skills_change_only_inside_quiesced_rollback_window():
    operator = (ROOT / "deploy/argon-alpha-14/deploy-runtime.sh").read_text()
    preflight = (ROOT / "deploy/argon-alpha-14/preflight-staged-runtime.sh").read_text()
    subuser = (ROOT / "deploy/takyon-subuser/deploy-runtime.sh").read_text()

    assert 'TAKYON_HOME="$TAKYON_SKILLS_PREFLIGHT_HOME"' in preflight
    assert 'TAKYON_HOME="$TAKYON_REMOTE_HOME" HOME=/opt/takyon' not in preflight
    assert operator.index("TAKYON_STOP_CORE_SERVICES=1") < operator.index(
        "TAKYON_FORCE_RESTORE_BUNDLED_SKILLS=1 '$TAKYON_REMOTE_RUNTIME/.venv/bin/python'"
    )
    assert operator.index("cp -a '$TAKYON_REMOTE_HOME/skills' '$remote_skills_backup'") < operator.index(
        "TAKYON_FORCE_RESTORE_BUNDLED_SKILLS=1 '$TAKYON_REMOTE_RUNTIME/.venv/bin/python'"
    )
    assert "rm -rf '$TAKYON_REMOTE_HOME/skills'" in operator

    assert "TAKYON_HOME='$remote_skills_preflight_home'" in subuser
    assert subuser.index("systemctl stop '$TAKYON_REMOTE_SERVICE_NAME'") < subuser.index(
        "PYTHONPATH='$TAKYON_REMOTE_RUNTIME' '$TAKYON_REMOTE_RUNTIME/.venv/bin/python'"
    )
    assert "cp -a '$TAKYON_REMOTE_HOME/skills' '$remote_skills_backup'" in subuser
    assert "rm -rf '$TAKYON_REMOTE_HOME/skills'" in subuser
