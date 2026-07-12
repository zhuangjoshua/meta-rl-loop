from __future__ import annotations

import signal
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins.takyon import core, jobs


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
    """Timeout cancellation must not leave Docker or a grandchild editor alive."""
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

    assert group_signals == [
        (proc.pid, signal.SIGTERM),
        (proc.pid, signal.SIGKILL),
    ]
    assert docker_calls == [
        ["/usr/bin/docker", "kill", "container-123"],
        ["/usr/bin/docker", "rm", "-f", "container-123"],
    ]
    assert proc.reaped is True
    assert proc.wait_calls[-1] is None


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


def test_standalone_claude_design_prompt_does_not_presume_taste():
    names, guidance = core._compose_worker_guidance_block(["claude-design"])

    assert names == ["claude-design"]
    assert "[Design guidance hierarchy]" not in guidance
    assert "otherwise do not invent or wait for a Taste layer" in guidance


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
