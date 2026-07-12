#!/usr/bin/env python3
"""Build and promote one immutable runtime artifact under the Mac deploy lock."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time


class _Interrupted(RuntimeError):
    def __init__(self, signum: int) -> None:
        super().__init__(f"interrupted by signal {signum}")
        self.signum = signum


def _run_git(runtime_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(runtime_dir), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def _run_git_optional(runtime_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(runtime_dir), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _repo_identity(runtime_dir: Path) -> tuple[Path, Path, Path, str, str, str]:
    # The outer Takyon repository is the deployment source of truth.  The canonical checkout may
    # contain stale nested git metadata under hermes-agent-main, so asking git from runtime_dir can
    # silently select the wrong repository while linked QA worktrees select the outer one.  Start
    # from the runtime's parent and require the outer tree to own this runtime path.
    repo_hint = runtime_dir.resolve().parent
    repo_root = Path(_run_git(repo_hint, "rev-parse", "--show-toplevel")).resolve()
    common_raw = Path(_run_git(repo_root, "rev-parse", "--git-common-dir"))
    if not common_raw.is_absolute():
        common_raw = repo_root / common_raw
    common_dir = common_raw.resolve()
    try:
        runtime_relative = runtime_dir.resolve().relative_to(repo_root)
    except ValueError as exc:
        raise RuntimeError(f"runtime is outside its git worktree: {runtime_dir}") from exc

    dirty = _run_git(
        repo_root,
        "status",
        "--porcelain",
        "--untracked-files=all",
    )
    if dirty:
        raise RuntimeError(
            "refusing deploy artifact from a dirty worktree; commit the intended deploy "
            f"revision first:\n{dirty}"
        )

    revision = _run_git(repo_root, "rev-parse", "HEAD")
    repository_tree = _run_git(repo_root, "rev-parse", f"{revision}^{{tree}}")
    runtime_tree = _run_git(
        repo_root, "rev-parse", f"{revision}:{runtime_relative.as_posix()}"
    )
    return (
        repo_root,
        common_dir,
        runtime_relative,
        revision,
        repository_tree,
        runtime_tree,
    )


def _assert_promotable_revision(repo_root: Path, revision: str) -> None:
    """Reject a queued worktree after the published deployment ref advances.

    A feature/QA worktree is valid when its commit is the published target commit; the local
    branch name is deliberately irrelevant. Production callers default to ``origin/main`` while a
    future dev caller can explicitly select ``origin/dev``. This comparison happens while holding
    the cross-worktree deploy lock, immediately before archiving.
    """

    target_ref = str(
        os.environ.get("TAKYON_DEPLOY_TARGET_REF") or "refs/remotes/origin/main"
    ).strip()
    if not target_ref:
        raise RuntimeError("TAKYON_DEPLOY_TARGET_REF must name one published git ref")
    target_revision = _run_git_optional(
        repo_root, "rev-parse", "--verify", f"{target_ref}^{{commit}}"
    )
    if not target_revision:
        raise RuntimeError(
            f"deployment target ref is unavailable: {target_ref}; fetch/push the intended "
            "main/dev revision before deploying"
        )
    if revision != target_revision:
        raise RuntimeError(
            "refusing stale or unpublished promotion: "
            f"worktree HEAD={revision}, {target_ref}={target_revision}"
        )


def _lock_path(common_dir: Path) -> Path:
    # Deliberately ignore TMPDIR: every linked worktree on this Mac must rendezvous here.
    lock_root = Path(
        os.environ.get(
            "TAKYON_WEB_BUILD_LOCK_ROOT",
            str(Path.home() / ".takyon-deploy-locks"),
        )
    )
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        lock_root.chmod(0o700)
    except OSError:
        pass
    key = hashlib.sha256(os.fsencode(str(common_dir))).hexdigest()[:24]
    return lock_root / f"{key}.lock"


def _archive_repo(*, repo_root: Path, revision: str, artifact_root: Path) -> None:
    artifact_root.mkdir(parents=True)
    archive_path = artifact_root.parent / "repo.tar"
    try:
        with archive_path.open("wb") as archive:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_root),
                    "archive",
                    "--format=tar",
                    revision,
                ],
                check=True,
                stdout=archive,
            )
        with tarfile.open(archive_path, mode="r:") as archive:
            archive.extractall(artifact_root)
    finally:
        archive_path.unlink(missing_ok=True)


def _web_dist_digest(web_dist: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(path for path in web_dist.rglob("*") if path.is_file())
    if not files:
        raise RuntimeError(f"web build produced no files: {web_dist}")
    for path in files:
        relative = path.relative_to(web_dist).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _seal_tree(root: Path) -> None:
    # A unique path prevents cross-deploy mutation; read-only modes make that contract explicit.
    for directory, dirnames, filenames in os.walk(root, topdown=False):
        directory_path = Path(directory)
        for name in filenames:
            path = directory_path / name
            if path.is_symlink():
                continue
            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
        for name in dirnames:
            path = directory_path / name
            if path.is_symlink():
                continue
            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    root.chmod(stat.S_IMODE(root.stat().st_mode) & ~0o222)


def _process_group_exists(child: subprocess.Popen[bytes]) -> bool:
    if os.name != "posix":
        return child.poll() is None
    try:
        os.killpg(child.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_process_group(
    child: subprocess.Popen[bytes],
    signum: int,
    *,
    grace_seconds: float,
) -> None:
    """Reap a private subprocess session, including descendants after its leader exits."""
    if not _process_group_exists(child):
        return
    try:
        if os.name == "posix":
            os.killpg(child.pid, signum)
        else:
            child.send_signal(signum)
    except ProcessLookupError:
        return

    deadline = time.monotonic() + max(0.0, grace_seconds)
    while _process_group_exists(child) and time.monotonic() < deadline:
        if child.poll() is None:
            try:
                child.wait(timeout=min(0.1, max(0.01, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
        else:
            time.sleep(0.02)
    if _process_group_exists(child):
        try:
            if os.name == "posix":
                os.killpg(child.pid, signal.SIGKILL)
            else:
                child.kill()
        except ProcessLookupError:
            pass
    if child.poll() is None:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def _run_build_command(command: list[str], *, cwd: Path) -> None:
    """Run one build phase in a private session and never leave npm descendants behind."""
    child = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=sys.stderr,
        stderr=sys.stderr,
        start_new_session=(os.name == "posix"),
    )
    try:
        returncode = child.wait()
    except BaseException:
        try:
            grace = max(
                0.0,
                float(os.environ.get("TAKYON_DEPLOY_INTERRUPT_GRACE_SECONDS", "10")),
            )
        except ValueError:
            grace = 10.0
        _terminate_process_group(child, signal.SIGTERM, grace_seconds=grace)
        raise
    # npm scripts must not daemonize work that can keep mutating the artifact after the phase
    # leader exits. A normal completed command has no remaining process group, so this is a no-op.
    _terminate_process_group(child, signal.SIGTERM, grace_seconds=1.0)
    if returncode:
        raise subprocess.CalledProcessError(returncode, command)


def _prepare_artifact(
    runtime_dir: Path,
    *,
    repo_root: Path,
    runtime_relative: Path,
    revision: str,
    repository_tree: str,
    runtime_tree: str,
) -> tuple[Path, Path, Path]:
    artifact_parent = Path(
        tempfile.mkdtemp(
            prefix=f"takyon-runtime-{revision[:12]}-",
            dir=os.environ.get("TAKYON_WEB_ARTIFACT_ROOT") or None,
        )
    )
    artifact_root = artifact_parent / "repo"
    artifact_runtime = artifact_root / runtime_relative
    try:
        _archive_repo(
            repo_root=repo_root,
            revision=revision,
            artifact_root=artifact_root,
        )
        if not artifact_runtime.is_dir():
            raise RuntimeError(
                f"revision {revision} has no runtime tree at {runtime_relative.as_posix()}"
            )
        web_dir = artifact_runtime / "web"
        if not (web_dir / "package-lock.json").is_file():
            raise RuntimeError(f"web package lock not found: {web_dir / 'package-lock.json'}")

        _run_build_command(["npm", "ci"], cwd=web_dir)
        _run_build_command(["npm", "run", "build"], cwd=web_dir)
        shutil.rmtree(web_dir / "node_modules", ignore_errors=True)

        web_dist = artifact_runtime / "takyon_cli" / "web_dist"
        manifest = {
            "schema": 1,
            "source_revision": revision,
            "repository_tree": repository_tree,
            "runtime_path": runtime_relative.as_posix(),
            "runtime_tree": runtime_tree,
            "web_dist_sha256": _web_dist_digest(web_dist),
            "prepared_at_unix": int(time.time()),
        }
        (artifact_runtime / ".takyon-deploy-artifact.json").write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        # Seal the whole revision, not just hermes-agent-main: service units, deploy helpers, and
        # the runtime must be promoted from one immutable commit.
        _seal_tree(artifact_root)
        return artifact_parent, artifact_root, artifact_runtime
    except BaseException:
        shutil.rmtree(artifact_parent, ignore_errors=True)
        raise


def _remove_artifact(artifact_parent: Path) -> None:
    if not artifact_parent.exists():
        return
    for directory, dirnames, filenames in os.walk(artifact_parent):
        directory_path = Path(directory)
        if not directory_path.is_symlink():
            try:
                directory_path.chmod(stat.S_IMODE(directory_path.stat().st_mode) | 0o700)
            except OSError:
                pass
        for name in filenames:
            path = directory_path / name
            if path.is_symlink():
                continue
            try:
                path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o600)
            except OSError:
                pass
        for name in dirnames:
            path = directory_path / name
            if path.is_symlink():
                continue
            try:
                path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o700)
            except OSError:
                pass
    shutil.rmtree(artifact_parent, ignore_errors=True)


def _artifact_command(
    command: list[str], *, repo_root: Path, artifact_root: Path
) -> list[str]:
    mapped = list(command)
    candidate = Path(mapped[0])
    if not candidate.is_absolute():
        if os.sep not in mapped[0]:
            return mapped
        candidate = Path.cwd() / candidate
    try:
        relative = candidate.resolve().relative_to(repo_root)
    except ValueError:
        return mapped
    artifact_candidate = artifact_root / relative
    if not artifact_candidate.is_file():
        raise RuntimeError(
            f"promotion command is not present in captured revision: {relative.as_posix()}"
        )
    mapped[0] = str(artifact_candidate)
    return mapped


def _run_promotion(
    command: list[str],
    *,
    repo_root: Path,
    artifact_root: Path,
    artifact_runtime: Path,
    revision: str,
) -> int:
    env = {
        **os.environ,
        "TAKYON_DEPLOY_LOCK_HELD": "1",
        "TAKYON_DEPLOY_REPO_ARTIFACT": str(artifact_root),
        "TAKYON_DEPLOY_RUNTIME_ARTIFACT": str(artifact_runtime),
        "TAKYON_DEPLOY_SOURCE_REVISION": revision,
        "TAKYON_RUN_WEB_BUILD": "0",
    }
    child: subprocess.Popen[bytes] | None = None

    def interrupt(signum: int, _frame: object) -> None:
        raise _Interrupted(signum)

    previous_handlers = {
        signum: signal.signal(signum, interrupt)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        child = subprocess.Popen(
            _artifact_command(command, repo_root=repo_root, artifact_root=artifact_root),
            env=env,
            start_new_session=(os.name == "posix"),
        )
        returncode = child.wait()
        # Promotion commands must not daemonize work that continues reading the soon-to-be-removed
        # artifact. Reap the whole private session even when its group leader returned normally.
        _terminate_process_group(child, signal.SIGTERM, grace_seconds=1.0)
        return returncode
    except _Interrupted as exc:
        # Do not key escalation only to the group leader: it can exit promptly while an ssh/rsync
        # descendant ignores the first signal and keeps mutating production.
        try:
            interrupt_grace = max(
                0.0, float(os.environ.get("TAKYON_DEPLOY_INTERRUPT_GRACE_SECONDS", "10"))
            )
        except ValueError:
            interrupt_grace = 10.0
        if child is not None:
            _terminate_process_group(child, exc.signum, grace_seconds=interrupt_grace)
        return 128 + exc.signum
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build an immutable runtime tree, then run its deploy/promotion command while "
            "holding the repo-global Mac lock."
        )
    )
    parser.add_argument("runtime_dir", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("a promotion command is required after --")
    return args


def main() -> int:
    args = _parse_args()
    runtime_dir = args.runtime_dir.resolve()
    if not runtime_dir.is_dir():
        raise RuntimeError(f"runtime directory not found: {runtime_dir}")

    (
        repo_root,
        common_dir,
        runtime_relative,
        _initial_revision,
        _initial_repository_tree,
        _initial_runtime_tree,
    ) = _repo_identity(runtime_dir)
    lock_path = _lock_path(common_dir)
    artifact_parent: Path | None = None

    def interrupt_waiter(signum: int, _frame: object) -> None:
        raise _Interrupted(signum)

    previous_handlers = {
        signum: signal.signal(signum, interrupt_waiter)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        # The file is persistent. Kernel ownership, not path deletion, is the authority, so an
        # interrupted waiter cannot remove or weaken the current owner's lock.
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            waiting_marker = str(
                os.environ.get("TAKYON_DEPLOY_WAITING_MARKER") or ""
            ).strip()
            if waiting_marker:
                Path(waiting_marker).touch()
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            except _Interrupted as exc:
                return 128 + exc.signum

            # Re-read identity after waiting. A queued invocation must never deploy the old HEAD
            # after this worktree or the published target ref advances while another promotion owns
            # the lock.
            (
                locked_repo_root,
                locked_common_dir,
                locked_runtime_relative,
                revision,
                repository_tree,
                runtime_tree,
            ) = _repo_identity(runtime_dir)
            if (
                locked_repo_root != repo_root
                or locked_common_dir != common_dir
                or locked_runtime_relative != runtime_relative
            ):
                raise RuntimeError("repository identity changed while waiting for deploy lock")
            if revision != _initial_revision:
                raise RuntimeError(
                    "worktree HEAD changed while waiting for deploy lock; rerun explicitly at "
                    f"the new revision (was {_initial_revision}, now {revision})"
                )
            _assert_promotable_revision(repo_root, revision)

            lock_file.seek(0)
            lock_file.truncate()
            lock_file.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "repo": str(common_dir),
                        "revision": revision,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            lock_file.flush()
            os.fsync(lock_file.fileno())

            artifact_parent, artifact_root, artifact_runtime = _prepare_artifact(
                runtime_dir,
                repo_root=repo_root,
                runtime_relative=runtime_relative,
                revision=revision,
                repository_tree=repository_tree,
                runtime_tree=runtime_tree,
            )
            return _run_promotion(
                args.command,
                repo_root=repo_root,
                artifact_root=artifact_root,
                artifact_runtime=artifact_runtime,
                revision=revision,
            )
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if artifact_parent is not None:
            _remove_artifact(artifact_parent)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except _Interrupted as exc:
        raise SystemExit(128 + exc.signum) from None
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"deploy artifact preparation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
