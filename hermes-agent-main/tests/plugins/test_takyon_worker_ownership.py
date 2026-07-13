from __future__ import annotations

import contextvars
import json
import signal
import subprocess
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins.takyon import core, jobs, worker
from plugins.takyon import claim_scope

_REAL_RUNTIME_RELEASE_SHA = claim_scope.runtime_release_sha


def test_all_canonical_product_writers_share_one_lane_separate_from_ceo():
    assert {
        jobs.job_lane("claude.agent_task"),
        jobs.job_lane("product.surface_refresh"),
        jobs.job_lane("store.build"),
    } == {"product"}
    assert jobs.job_lane("ceo_bootstrap") == "ceo"
    assert jobs.job_lane("ceo_bootstrap") != jobs.job_lane("claude.agent_task")


class _RowsConn:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, _sql, _params=()):
        return SimpleNamespace(fetchone=lambda: self.row)


class _RowsStore:
    def __init__(self, row):
        self.row = row

    def _connect(self):
        return _RowsConn(self.row)


def test_platform_publish_blocker_requires_a_new_validation_passed_refresh():
    payload = {
        "status": "passed",
        "publish": {"status": "blocked", "blocker": "database activation failed"},
    }
    store = _RowsStore({"id": "event-2", "payload_json": payload})

    assert worker._product_publish_blocker_after(store, "demo", "event-1") == (
        "event-2",
        "database activation failed",
    )
    assert worker._product_publish_blocker_after(store, "demo", "event-2") == (
        "event-2",
        "",
    )


def test_product_worker_uses_an_immutable_claimed_release_snapshot(tmp_path, monkeypatch):
    release = "a" * 40
    runtime = tmp_path / "runtime"
    (runtime / "scripts").mkdir(parents=True)
    canonical_skill = (
        Path(core.__file__).resolve().parents[2]
        / "skills"
        / "creative"
        / "taste-frontend"
        / "SKILL.md"
    ).read_bytes()
    taste_gate = (Path(core.__file__).resolve().parent / "taste_publication_gate.py").read_bytes()
    files = {
        "package.json": b'{"dependencies":{"@anthropic-ai/claude-agent-sdk":"1.0.0"}}',
        "package-lock.json": b'{"lockfileVersion":3,"packages":{}}',
        "plugins/takyon/taste_publication_gate.py": taste_gate,
        "scripts/takyon-claude-agent-task.mjs": b"console.log('sealed');\n",
        "skills/creative/taste-frontend/SKILL.md": canonical_skill,
    }
    for relative, content in files.items():
        (runtime / relative).parent.mkdir(parents=True, exist_ok=True)
        (runtime / relative).write_bytes(content)
    (runtime / ".takyon-deploy-artifact.json").write_text(
        json.dumps({"source_revision": release}), encoding="utf-8"
    )
    monkeypatch.setattr(claim_scope, "runtime_release_sha", lambda **_kwargs: release)
    monkeypatch.setattr(core, "get_default_takyon_root", lambda: tmp_path / "home")
    monkeypatch.setattr(core, "_resolve_runtime_executable", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(core, "_shared_npm_cache_dir", lambda: tmp_path / "npm-cache")

    def fake_run(command, **kwargs):
        assert command[1:3] == ["ci", "--ignore-scripts"]
        sdk = Path(kwargs["cwd"]) / "node_modules" / "@anthropic-ai" / "claude-agent-sdk"
        sdk.mkdir(parents=True)
        (sdk / "package.json").write_text('{"version":"1.0.0"}', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    snapshot = core._product_worker_runtime_snapshot(runtime)
    (runtime / "scripts" / "takyon-claude-agent-task.mjs").write_text(
        "console.log('mutated');\n", encoding="utf-8"
    )

    assert core._product_worker_runtime_snapshot(runtime) == snapshot
    assert (snapshot / "scripts" / "takyon-claude-agent-task.mjs").read_bytes() == files[
        "scripts/takyon-claude-agent-task.mjs"
    ]
    assert (snapshot / "plugins" / "takyon" / "taste_publication_gate.py").read_bytes() == taste_gate
    native = snapshot / ".claude" / "skills" / "design-taste-frontend"
    assert native.is_symlink()
    assert native.readlink() == Path("../../skills/creative/taste-frontend")
    assert native.resolve() == snapshot / "skills" / "creative" / "taste-frontend"


def test_non_docker_native_taste_install_is_shared_under_takyon_home(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    canonical_dir = runtime / "skills" / "creative" / "taste-frontend"
    canonical_dir.mkdir(parents=True)
    source_skill = (
        Path(core.__file__).resolve().parents[2]
        / "skills"
        / "creative"
        / "taste-frontend"
        / "SKILL.md"
    )
    (canonical_dir / "SKILL.md").write_bytes(source_skill.read_bytes())
    takyon_home = tmp_path / "operator-home"
    monkeypatch.setenv("TAKYON_HOME", str(takyon_home))

    config = core._shared_claude_config_dir(runtime)
    native = config / "skills" / "design-taste-frontend"

    assert config == takyon_home / ".claude"
    assert native.is_symlink()
    assert native.resolve() == canonical_dir
    native.unlink()
    native.symlink_to(tmp_path / "forbidden-business-override", target_is_directory=True)
    assert core._shared_claude_config_dir(runtime) == config
    assert native.resolve() == canonical_dir
    monkeypatch.setattr(core, "_repo_root", lambda: runtime)
    monkeypatch.setattr(
        core,
        "_mint_claude_agent_operator_session_token",
        lambda _business, _operator_user_id: "operator-session-capability",
    )
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER", "1")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER_URL", "http://10.116.0.2:8000")
    env = core._claude_agent_non_docker_worker_env("native-taste", "owner-1")
    assert env["CLAUDE_CONFIG_DIR"] == str(config)
    source = Path(core.__file__).read_text(encoding="utf-8")
    assert '"claudeConfigDir": worker_env["CLAUDE_CONFIG_DIR"]' in source


def test_docker_native_taste_config_is_writable_but_skill_is_release_readonly(
    tmp_path, monkeypatch
):
    from tools.environments import docker as docker_env

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    snapshot = tmp_path / "snapshot"
    (snapshot / ".claude" / "skills").mkdir(parents=True)
    monkeypatch.setattr(docker_env, "find_docker", lambda: "/usr/bin/docker")
    monkeypatch.setattr(docker_env, "_resolve_host_user_spec", lambda: None)
    monkeypatch.setattr(docker_env, "_host_user_identity_mount_args", lambda _spec: [])
    monkeypatch.setattr(docker_env, "_build_security_args", lambda _as_user=False: [])
    monkeypatch.setattr(core, "_repo_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(core, "_product_worker_runtime_snapshot", lambda _root: snapshot)
    monkeypatch.setattr(core, "_docker_claude_worker_binary_mounts", lambda **_kwargs: ([], {}))
    monkeypatch.setattr(core, "_runtime_env", lambda extra=None: dict(extra or {}))
    monkeypatch.setattr(core, "_shared_npm_cache_dir", lambda: tmp_path / "npm-cache")
    monkeypatch.setattr(
        core,
        "_mint_claude_agent_operator_session_token",
        lambda _business, _operator_user_id: "operator-session-capability",
    )
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER", "1")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER_URL", "http://10.116.0.2:8000")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_MODEL", "deepseek-v4-pro")

    command, payload, _worker_cwd, _worker_env = core._run_claude_agent_task_in_docker(
        payload={"business": "native-taste", "workspace": "product/site", "instruction": "x"},
        workspace_path=workspace,
        timeout_ms=30_000,
        business="native-taste",
        operator_user_id="owner-1",
    )

    assert payload["claudeConfigDir"] == "/repo/.claude"
    assert "CLAUDE_CONFIG_DIR=/repo/.claude" in command
    assert "/repo/.claude:rw,nosuid,nodev,noexec,mode=1777,size=64m" in command
    assert f"type=bind,src={snapshot},dst=/repo,readonly" in command
    assert (
        f"type=bind,src={snapshot / '.claude' / 'skills'},dst=/repo/.claude/skills,readonly"
        in command
    )


def test_existing_business_upsert_does_not_hydrate_workspace(tmp_path, monkeypatch):
    store = object.__new__(core.TakyonStore)
    monkeypatch.setattr(store, "_business", lambda *_args: {"slug": "latexflow"})
    monkeypatch.setattr(store, "_business_workspace_base", lambda: tmp_path / "businesses")
    monkeypatch.setattr(store, "_record_event", lambda *_args, **_kwargs: "event")
    monkeypatch.setattr(
        store,
        "_business_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("workspace hydrated")),
    )
    executed: list[tuple[str, tuple]] = []

    class Conn:
        def execute(self, sql, params=()):
            executed.append((sql, params))
            return SimpleNamespace(rowcount=1)

    result = store._apply_operation(
        Conn(),
        {"raw": "business:latexflow", "business": "latexflow"},
        {
            "action": "business.upsert",
            "business": "latexflow",
            "business_slug": "latexflow",
            "target_scope": "business:latexflow",
            "name": "Latex Flow",
        },
        reason="metadata-only-update",
        actor="test",
    )

    assert result["business"] == "latexflow"
    assert any("UPDATE businesses" in sql for sql, _params in executed)


def test_durable_write_fence_rejects_same_worker_newer_attempt():
    """BriefVault regression: worker id reuse must not let attempt 1 write through attempt 2."""
    guard = jobs.JobClaimGuard(job_id="briefvault-job", worker_id="mac-worker", attempt=1)
    store = _RowsStore(
        {"status": "running", "locked_by": "mac-worker", "attempts": 2}
    )

    with jobs._bound_job_claim(guard):
        with pytest.raises(jobs.JobClaimLost, match="newer or terminal"):
            core._assert_active_worker_claim(store, "workspace sync")

    assert guard.lost is True


def test_durable_write_fence_reaps_cancelled_child_before_side_effect():
    guard = jobs.JobClaimGuard(job_id="taste-child", worker_id="mac-worker", attempt=1)
    store = _RowsStore(
        {
            "status": "running",
            "locked_by": "mac-worker",
            "attempts": 1,
            "payload": {"cancel_requested": True},
        }
    )

    with jobs._bound_job_claim(guard):
        with pytest.raises(jobs.JobClaimLost, match="cancellation"):
            core._assert_active_worker_claim(store, "publish timed-out Taste source")

    assert guard.lost is True


class _LeaseTransaction:
    def __init__(self, exited: threading.Event):
        self.exited = exited

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exited.set()
        return False


class _MonitoredLeaseConn:
    def __init__(self):
        self.fail_probe = threading.Event()
        self.transaction_exited = threading.Event()
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def transaction(self):
        return _LeaseTransaction(self.transaction_exited)

    def execute(self, sql, _params=()):
        normalized = str(sql).lower()
        if "pg_try_advisory_xact_lock" in normalized:
            row = (True,)
        elif "pg_advisory_xact_lock" in normalized:
            row = None
        elif "pg_backend_pid" in normalized:
            if self.fail_probe.is_set():
                raise RuntimeError("lease socket closed")
            row = (4312, "991", True)
        else:
            raise AssertionError(f"unexpected lease SQL: {sql}")
        return SimpleNamespace(fetchone=lambda: row)

    def close(self):
        self.closed = True


def test_product_writer_same_connection_loss_cancels_bound_claim_before_unwind(monkeypatch):
    monkeypatch.setattr(jobs, "_PRODUCT_WRITER_LEASE_PROBE_SECONDS", 0.01)
    conn = _MonitoredLeaseConn()
    claim = jobs.JobClaimGuard(job_id="writer-job", worker_id="worker-a", attempt=1)
    job = SimpleNamespace(kind="claude.agent_task", business_slug="alpha")
    child_entered = threading.Event()
    child_aborted = threading.Event()

    with pytest.raises(jobs.JobClaimLost, match="product-writer lease lost"):
        with jobs._hold_product_writer_lease(
            job,
            fallback_conn=conn,
            conn_factory=None,
            claim_guard=claim,
        ):
            child_context = contextvars.copy_context()

            def _child() -> None:
                child_entered.set()
                guard = jobs.current_execution_lease_guard()
                assert guard is not None
                while True:
                    try:
                        guard.assert_owned("test child")
                    except jobs.JobClaimLost:
                        assert not conn.transaction_exited.is_set()
                        child_aborted.set()
                        return
                    time.sleep(0.005)

            child = threading.Thread(target=lambda: child_context.run(_child))
            child.start()
            assert child_entered.wait(1)
            conn.fail_probe.set()
            assert child_aborted.wait(2)
            child.join(1)
            assert not child.is_alive()

    assert claim.lost is True
    assert conn.transaction_exited.is_set()


def test_inline_product_writer_same_connection_loss_fails_closed(monkeypatch):
    monkeypatch.setattr(jobs, "_PRODUCT_WRITER_LEASE_PROBE_SECONDS", 0.01)
    conn = _MonitoredLeaseConn()
    store = SimpleNamespace(_connect=lambda: conn)

    with pytest.raises(jobs.JobClaimLost, match="product-writer lease lost"):
        with core._hold_business_product_writer_lease(store, business="alpha"):
            guard = jobs.current_execution_lease_guard()
            assert guard is not None
            conn.fail_probe.set()
            deadline = time.monotonic() + 2
            while not guard.lost and time.monotonic() < deadline:
                time.sleep(0.005)
            core._assert_active_product_writer_lease("inline source write")

    assert conn.transaction_exited.is_set()


class _CaptureConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def transaction(self):
        return nullcontext()

    def execute(self, sql, params=()):
        self.calls.append((str(sql), tuple(params)))
        return SimpleNamespace(rowcount=0, fetchone=lambda: None)


def test_stale_reaper_skips_live_local_handler_and_bootstrap_with_child(monkeypatch):
    """A quiet parent cannot be requeued alongside its still-running handler/delegated build."""
    monkeypatch.setattr(jobs, "_refresh_job_lifecycle_session", lambda _conn: None)
    conn = _CaptureConn()
    guard = jobs.JobClaimGuard(job_id="briefvault-parent", worker_id="worker-a", attempt=1)

    with jobs._bound_job_claim(guard):
        assert jobs.requeue_stale(conn, older_than_seconds=1) == 0

    update_calls = [call for call in conn.calls if call[0].lower().startswith("update jobs")]
    assert len(update_calls) == 2
    for sql, params in update_calls:
        assert "not (id = any(%s))" in sql
        assert "child.kind in ('claude.agent_task', 'product.surface_refresh')" in sql
        assert "child.status in ('queued', 'running')" in sql
        assert ["briefvault-parent"] in params


class _HungProcess:
    pid = 424242

    def __init__(self):
        self.reaped = False
        self.wait_calls: list[float | None] = []

    def poll(self):
        return None

    def terminate(self):
        raise AssertionError("POSIX process group should be signalled, not only the wrapper")

    def kill(self):
        raise AssertionError("POSIX process group should be killed, not only the wrapper")

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if timeout == 5:
            raise subprocess.TimeoutExpired(["docker", "run"], timeout)
        self.reaped = True
        return -int(signal.SIGKILL)


def test_worker_termination_kills_group_container_and_reaps(tmp_path, monkeypatch):
    """Hard fallback must not leave Docker or a grandchild editor alive."""
    proc = _HungProcess()
    cidfile = tmp_path / "worker.cid"
    cidfile.write_text("container-123\n", encoding="utf-8")
    group_signals: list[tuple[int, signal.Signals]] = []
    docker_calls: list[list[str]] = []

    monkeypatch.setattr(
        core.os,
        "killpg",
        lambda pid, sig: group_signals.append((pid, signal.Signals(sig))),
    )

    def fake_run(command, **_kwargs):
        docker_calls.append(list(command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(core.subprocess, "run", fake_run)

    core._terminate_claude_worker_process(
        proc,
        run_cmd=["/usr/bin/docker", "run", "--rm", "image"],
        cidfile=cidfile,
    )

    assert group_signals == [(proc.pid, signal.SIGKILL)]
    assert docker_calls == [
        ["/usr/bin/docker", "kill", "container-123"],
        ["/usr/bin/docker", "rm", "-f", "container-123"],
    ]
    assert proc.reaped is True
    assert proc.wait_calls[-1] is None


class _GracefulProcess:
    pid = 515151

    def __init__(self):
        self.exited = False
        self.wait_calls: list[float | None] = []

    def poll(self):
        return 0 if self.exited else None

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        self.exited = True
        return 143

    def terminate(self):
        raise AssertionError("Docker cancellation must signal the container, not only its client")


def test_worker_cancellation_soft_aborts_and_drains_before_hard_fallback(tmp_path, monkeypatch):
    proc = _GracefulProcess()
    cidfile = tmp_path / "worker.cid"
    cidfile.write_text("container-456\n", encoding="utf-8")
    docker_calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        docker_calls.append(list(command))
        if "inspect" in command:
            return SimpleNamespace(returncode=0, stdout="false\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    monkeypatch.setattr(
        core,
        "_terminate_claude_worker_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("graceful drain must not use hard fallback")
        ),
    )

    core._cancel_claude_worker_process(
        proc,
        run_cmd=["/usr/bin/docker", "run", "--rm", "image"],
        cidfile=cidfile,
        grace_seconds=2,
    )

    assert docker_calls == [
        ["/usr/bin/docker", "kill", "--signal=TERM", "container-456"],
        ["/usr/bin/docker", "wait", "container-456"],
        ["/usr/bin/docker", "inspect", "--format", "{{.State.Running}}", "container-456"],
        ["/usr/bin/docker", "rm", "-f", "container-456"],
    ]
    assert proc.exited is True


def test_node_worker_has_no_independent_timeout_clock():
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "takyon-claude-agent-task.mjs"
    ).read_text(encoding="utf-8")

    main_body = script.split("async function main()", 1)[1].split("\nexport {", 1)[0]
    assert "setTimeout(" not in main_body
    assert 'process.once("SIGTERM", requestParentAbort)' in script
    assert "abortController.abort()" in script


def test_every_business_claude_worker_fails_on_first_sdk_retry():
    source = Path(core.__file__).read_text(encoding="utf-8")

    assert '"failOnApiRetry": True' in source


def test_product_worker_preflights_release_and_never_starts_a_repair_model_call():
    source = Path(core.__file__).read_text(encoding="utf-8")
    owned = source.split("def _handle_business_claude_agent_task_owned", 1)[1].split(
        "\ndef ", 1
    )[0]

    assert owned.index("runtime_release_sha(runtime_root=_repo_root())") < owned.index(
        "_reserve_operator_task_budget("
    )
    assert "should_retry_surface_build" not in owned
    assert "should_retry_turn_cap" not in owned
    assert "Hermes automatic build-fix retry" not in owned
    assert "Hermes automatic continuation retry" not in source


def test_product_site_capabilities_are_selected_by_workspace_not_guidance():
    source = Path(core.__file__).read_text(encoding="utf-8")
    owned = source.split("def _handle_business_claude_agent_task_owned", 1)[1].split(
        "\ndef ", 1
    )[0]

    assert "guidance_skills" not in owned
    assert "if _workspace_needs_runtime_ui_contract(workspace_rel):\n" in owned
    assert "_site_image_worker_bridge(" in owned


def _clean_git_runtime(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "runtime"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "runtime.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "runtime"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return root, sha


def test_runtime_release_sha_accepts_only_matching_clean_head(tmp_path, monkeypatch):
    root, sha = _clean_git_runtime(tmp_path)
    monkeypatch.setenv(claim_scope.RUNTIME_RELEASE_SHA_ENV, sha)
    assert _REAL_RUNTIME_RELEASE_SHA(runtime_root=root) == sha

    monkeypatch.setenv(claim_scope.RUNTIME_RELEASE_SHA_ENV, "f" * 40)
    with pytest.raises(RuntimeError, match="does not match clean runtime HEAD"):
        _REAL_RUNTIME_RELEASE_SHA(runtime_root=root)


@pytest.mark.parametrize("dirty_kind", ["tracked", "staged", "untracked"])
def test_runtime_release_sha_rejects_modified_worktree(tmp_path, monkeypatch, dirty_kind):
    root, sha = _clean_git_runtime(tmp_path)
    monkeypatch.setenv(claim_scope.RUNTIME_RELEASE_SHA_ENV, sha)
    if dirty_kind == "tracked":
        (root / "runtime.py").write_text("VALUE = 2\n", encoding="utf-8")
    elif dirty_kind == "staged":
        (root / "runtime.py").write_text("VALUE = 2\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "runtime.py"], check=True)
    else:
        (root / "new-runtime.py").write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="modified runtime worktree"):
        _REAL_RUNTIME_RELEASE_SHA(runtime_root=root)


def test_runtime_release_sha_timeout_is_unavailable_not_invalid(tmp_path, monkeypatch):
    root, sha = _clean_git_runtime(tmp_path)
    monkeypatch.setenv(claim_scope.RUNTIME_RELEASE_SHA_ENV, sha)
    real_run = subprocess.run

    def timeout_status(command, *args, **kwargs):
        if "status" in command:
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 10))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(claim_scope.subprocess, "run", timeout_status)

    with pytest.raises(
        claim_scope.LocalReleaseIdentityUnavailable,
        match="timed out verifying runtime worktree cleanliness",
    ):
        _REAL_RUNTIME_RELEASE_SHA(runtime_root=root)


def test_core_does_not_inject_taste_or_guidance_skill_prose():
    source = Path(core.__file__).read_text(encoding="utf-8")
    task = next(
        item
        for item in core.TAKYON_TOOL_DEFINITIONS
        if item["name"] == "business_claude_agent_task"
    )

    assert "guidance_skills" not in task["schema"]["parameters"]["properties"]
    assert "[Hermes guidance skill:" not in source
    assert "_compose_worker_guidance_block" not in source
    assert "_resolve_worker_guidance_skills" not in source


def test_taste_design_contract_requires_all_dials_in_range(tmp_path):
    (tmp_path / "DESIGN.md").write_text(
        "# Design Read\nA precise editorial landing.\n\n"
        "DESIGN_VARIANCE: 6\nMOTION_INTENSITY: 11\n",
        encoding="utf-8",
    )

    contract, blocker = core._read_taste_design_contract(tmp_path)

    assert contract == {}
    assert blocker == (
        "Taste design contract invalid: MOTION_INTENSITY must be an integer from 1 to 10."
    )
