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
    files = {
        "package.json": b'{"dependencies":{"@anthropic-ai/claude-agent-sdk":"1.0.0"}}',
        "package-lock.json": b'{"lockfileVersion":3,"packages":{}}',
        "scripts/takyon-claude-agent-task.mjs": b"console.log('sealed');\n",
    }
    for relative, content in files.items():
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


def test_pinned_upstream_taste_body_is_injected_once_without_excerpt_truncation():
    skill_file = core._find_guidance_skill_file("taste-frontend")
    assert skill_file is not None
    _frontmatter, body = core.parse_frontmatter(skill_file.read_text(encoding="utf-8"))
    assert len(body) > 12_000, "regression requires the full upstream body to exceed old excerpt cap"
    names, guidance = core._compose_worker_guidance_block(["taste-frontend"])

    assert names == ["design-taste-frontend"]
    assert guidance.count(body.strip()) == 1
    assert body.strip()[-500:] in guidance
    assert "...[truncated]" not in guidance


def test_product_design_default_adds_no_legacy_template():
    names, reason = core._resolve_worker_guidance_skills({}, "product/site")

    assert names == []
    assert reason == "preserved the established Taste DESIGN.md without extra design guidance"


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
