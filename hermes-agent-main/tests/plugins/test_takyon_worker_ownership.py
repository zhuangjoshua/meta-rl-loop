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


def test_canonical_product_publication_jobs_share_one_lane_separate_from_ceo():
    assert {
        jobs.job_lane("product.surface_refresh"),
        jobs.job_lane("store.build"),
    } == {"product"}
    assert jobs.job_lane("ceo_bootstrap") == "ceo"
    assert jobs.job_lane("ceo_bootstrap") != jobs.job_lane("product.surface_refresh")


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
    job = SimpleNamespace(kind="product.surface_refresh", business_slug="alpha")
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
        assert "child.kind = 'product.surface_refresh'" in sql
        assert "child.status in ('queued', 'running')" in sql
        assert ["briefvault-parent"] in params


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


def test_core_has_no_nested_model_tool_or_guidance_injection():
    source = Path(core.__file__).read_text(encoding="utf-8")
    assert "business_claude_agent_task" not in {
        item["name"] for item in core.TAKYON_TOOL_DEFINITIONS
    }
    assert "[Hermes guidance skill:" not in source
    assert "_compose_worker_guidance_block" not in source
    assert "_resolve_worker_guidance_skills" not in source
