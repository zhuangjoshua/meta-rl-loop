"""Crash-safety of the shared warm node_modules prebake (the "stuck reserved sign" bug).

A force-killed ``npm ci`` (the ``stop-sigterm timed out. Killing.`` case during a deploy/restart) must
never:
  * strand a lock/marker that permanently reverts every later build to a slow cold install, nor
  * publish a half-installed tree that a consumer mistakes for complete and seeds into a business.

The prebake builds into a private temp sibling and publishes with one atomic ``os.replace``, so the
real arch+lockhash spot is always *absent-or-complete*. These tests pin that invariant.
"""
from __future__ import annotations

import os
import time
import types
from pathlib import Path

from plugins.takyon import core


def _mk_scaffold(tmp_path: Path) -> Path:
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir()
    (scaffold / "package.json").write_text('{"name":"x","version":"0.0.0"}')
    (scaffold / "package-lock.json").write_text('{"lockfileVersion":3}')
    return scaffold


def _wire(monkeypatch, tmp_path: Path, *, npm_run) -> Path:
    prebake = tmp_path / "cache" / "node-modules" / "deadbeefdeadbeef"
    scaffold = _mk_scaffold(tmp_path)
    monkeypatch.setattr(core, "_warm_node_modules_prebake_dir", lambda: prebake)
    monkeypatch.setattr(core, "_subuser_app_scaffold_source_dir", lambda: scaffold)
    monkeypatch.setattr(
        core, "_resolve_runtime_executable", lambda name: "npm" if name == "npm" else None
    )
    monkeypatch.setattr(core, "_javascript_install_env", lambda root: {})
    monkeypatch.setattr(core.subprocess, "run", npm_run)
    return prebake


def _npm_success(cmd, cwd=None, **kwargs):
    """Simulate a clean ``npm ci`` into the staging dir: build binaries present, rc=0."""
    binp = Path(cwd) / "node_modules" / ".bin"
    binp.mkdir(parents=True, exist_ok=True)
    (binp / "vite").write_text("#!/bin/sh\n")
    (binp / "tsc").write_text("#!/bin/sh\n")
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def _npm_killed(cmd, cwd=None, **kwargs):
    """Simulate a force-kill mid-install: a partial tree lands in staging, no binaries, nonzero rc."""
    (Path(cwd) / "node_modules" / "halfpkg").mkdir(parents=True, exist_ok=True)
    return types.SimpleNamespace(returncode=137, stdout="", stderr="Killed")


def test_happy_path_publishes_complete_tree(tmp_path, monkeypatch):
    prebake = _wire(monkeypatch, tmp_path, npm_run=_npm_success)
    out = core._ensure_warm_node_modules_prebake()
    assert out == prebake
    assert core._warm_node_modules_ready(prebake)
    # No staging junk left behind next to the real spot.
    assert [p for p in prebake.parent.iterdir() if ".partial-" in p.name] == []


def test_stranded_building_marker_does_not_block_rebuild(tmp_path, monkeypatch):
    # Pre-fix failure mode: a force-kill left a `.building` marker (and no usable tree) in the real
    # spot. The old code hit FileExistsError forever and never rebuilt — the "reserved sign".
    prebake = _wire(monkeypatch, tmp_path, npm_run=_npm_success)
    (prebake / ".building").mkdir(parents=True)
    assert not core._warm_node_modules_ready(prebake)
    out = core._ensure_warm_node_modules_prebake()
    assert out == prebake
    assert core._warm_node_modules_ready(prebake)
    assert not (prebake / ".building").exists()  # stale marker cleared, not honored


def test_half_installed_real_spot_is_replaced_not_trusted(tmp_path, monkeypatch):
    # A half-installed tree sitting in the real spot must be cleared, not reused or seeded.
    prebake = _wire(monkeypatch, tmp_path, npm_run=_npm_success)
    (prebake / "node_modules" / "halfpkg").mkdir(parents=True)
    assert not core._warm_node_modules_ready(prebake)
    out = core._ensure_warm_node_modules_prebake()
    assert out == prebake
    assert core._warm_node_modules_ready(prebake)
    assert not (prebake / "node_modules" / "halfpkg").exists()


def test_force_kill_leaves_real_spot_untouched_and_next_build_recovers(tmp_path, monkeypatch):
    prebake = _wire(monkeypatch, tmp_path, npm_run=_npm_killed)
    out = core._ensure_warm_node_modules_prebake()
    assert out is None
    # Crucial invariant: the real spot was NEVER created half-done — it is simply absent.
    assert not prebake.exists()
    # And nothing stranded blocks a retry: a later successful build recovers cleanly (no permanent
    # flip to cold install).
    monkeypatch.setattr(core.subprocess, "run", _npm_success)
    out2 = core._ensure_warm_node_modules_prebake()
    assert out2 == prebake
    assert core._warm_node_modules_ready(prebake)


def test_ready_real_spot_is_reused_without_rebuilding(tmp_path, monkeypatch):
    calls = {"n": 0}

    def _counting(cmd, cwd=None, **kwargs):
        calls["n"] += 1
        return _npm_success(cmd, cwd=cwd, **kwargs)

    prebake = _wire(monkeypatch, tmp_path, npm_run=_counting)
    binp = prebake / "node_modules" / ".bin"
    binp.mkdir(parents=True)
    (binp / "vite").write_text("x")
    (binp / "tsc").write_text("x")
    out = core._ensure_warm_node_modules_prebake()
    assert out == prebake
    assert calls["n"] == 0  # short-circuited — no npm ci against a ready prebake


def test_publish_reuses_a_complete_concurrent_winner(tmp_path, monkeypatch):
    """Rename-first publish: if the real spot already holds a COMPLETE tree by publish time (a
    concurrent build won the race), our finished staging is discarded and the winner's tree is REUSED
    — never deleted+replaced. Closes the redundant-delete TOCTOU the in-place rmtree had."""
    prebake = _wire(monkeypatch, tmp_path, npm_run=_npm_success)
    real_ready = core._warm_node_modules_ready
    state = {"n": 0}

    def staged_ready(p):
        state["n"] += 1
        return False if state["n"] == 1 else real_ready(p)  # top guard misses -> we build a staging

    win = prebake / "node_modules" / ".bin"
    win.mkdir(parents=True)
    (win / "vite").write_text("WINNER")
    (win / "tsc").write_text("WINNER")
    monkeypatch.setattr(core, "_warm_node_modules_ready", staged_ready)
    out = core._ensure_warm_node_modules_prebake()
    assert out == prebake
    assert (prebake / "node_modules" / ".bin" / "vite").read_text() == "WINNER"  # winner survived
    assert [p for p in prebake.parent.iterdir() if ".partial-" in p.name] == []  # no staging/aside junk


def test_sweep_removes_old_partials_keeps_fresh_and_real(tmp_path):
    parent = tmp_path / "node-modules"
    parent.mkdir()
    real = parent / "deadbeefdeadbeef"
    real.mkdir()
    old = parent / ".deadbeefdeadbeef.partial-OLD"
    old.mkdir()
    fresh = parent / ".deadbeefdeadbeef.partial-FRESH"
    fresh.mkdir()
    aged = time.time() - 7200
    os.utime(old, (aged, aged))
    core._sweep_warm_prebake_partials(parent, keep=real.name)
    assert not old.exists()   # stale orphan from a prior kill reclaimed
    assert fresh.exists()     # in-flight concurrent staging preserved (age-guarded)
    assert real.exists()      # real spot untouched
