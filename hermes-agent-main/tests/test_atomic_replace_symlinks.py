"""Regression tests for GitHub #16743 — atomic writes must preserve symlinks.

``os.replace(tmp, target)`` replaces whatever exists at ``target`` — including
symlinks, which it swaps for a regular file.  Managed deployments that
symlink ``~/.takyon/config.yaml`` (and other state files) to a git-tracked
profile package were silently detached on every config write.

The fix: a shared ``atomic_replace`` helper in ``utils.py`` that resolves the
target through ``os.path.realpath`` when it is a symlink, so the real file is
overwritten in-place while the symlink survives.  All atomic-write sites in
the codebase were migrated to the helper; these tests pin that invariant.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

# Ensure the repo root is importable when running via `pytest tests/...`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils import atomic_json_write, atomic_replace, atomic_yaml_write


# ─── Direct helper ────────────────────────────────────────────────────────────


def _write_tmp(dir_: Path, content: str) -> Path:
    tmp = dir_ / ".src.tmp"
    tmp.write_text(content, encoding="utf-8")
    return tmp


def test_atomic_replace_preserves_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.yaml"
    link = tmp_path / "link.yaml"
    real.write_text("original\n", encoding="utf-8")
    link.symlink_to(real)

    tmp = _write_tmp(tmp_path, "updated\n")
    returned = atomic_replace(tmp, link)

    assert link.is_symlink(), "symlink must not be replaced with a regular file"
    assert real.read_text(encoding="utf-8") == "updated\n"
    assert Path(returned) == real
    # Follow the symlink — same content.
    assert link.read_text(encoding="utf-8") == "updated\n"


def test_atomic_replace_regular_file(tmp_path: Path) -> None:
    target = tmp_path / "plain.yaml"
    target.write_text("old\n", encoding="utf-8")

    tmp = _write_tmp(tmp_path, "fresh\n")
    returned = atomic_replace(tmp, target)

    assert Path(returned) == target
    assert target.read_text(encoding="utf-8") == "fresh\n"
    assert not target.is_symlink()


def test_atomic_replace_first_time_create(tmp_path: Path) -> None:
    target = tmp_path / "new.yaml"
    assert not target.exists()

    tmp = _write_tmp(tmp_path, "brand new\n")
    returned = atomic_replace(tmp, target)

    assert Path(returned) == target
    assert target.read_text(encoding="utf-8") == "brand new\n"


def test_atomic_replace_accepts_pathlike_and_str(tmp_path: Path) -> None:
    target = tmp_path / "dual.json"
    target.write_text("{}", encoding="utf-8")

    # str inputs
    tmp1 = _write_tmp(tmp_path, "1")
    atomic_replace(str(tmp1), str(target))
    assert target.read_text(encoding="utf-8") == "1"

    # Path inputs
    tmp2 = _write_tmp(tmp_path, "2")
    atomic_replace(tmp2, target)
    assert target.read_text(encoding="utf-8") == "2"


# ─── atomic_json_write / atomic_yaml_write wiring ──────────────────────────


def test_atomic_json_write_preserves_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    link = tmp_path / "link.json"
    real.write_text("{}", encoding="utf-8")
    link.symlink_to(real)

    atomic_json_write(link, {"hello": "world"})

    assert link.is_symlink()
    loaded = json.loads(real.read_text(encoding="utf-8"))
    assert loaded == {"hello": "world"}


def test_atomic_yaml_write_preserves_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.yaml"
    link = tmp_path / "link.yaml"
    real.write_text("placeholder: true\n", encoding="utf-8")
    link.symlink_to(real)

    atomic_yaml_write(link, {"model": {"provider": "openrouter"}})

    assert link.is_symlink()
    data = yaml.safe_load(real.read_text(encoding="utf-8"))
    assert data == {"model": {"provider": "openrouter"}}


def test_atomic_json_write_preserves_symlink_permissions(tmp_path: Path) -> None:
    """Symlinked targets keep the real file's permission bits."""
    if os.name != "posix":
        pytest.skip("POSIX-only")

    real = tmp_path / "real.json"
    link = tmp_path / "link.json"
    real.write_text("{}", encoding="utf-8")
    os.chmod(real, 0o644)
    link.symlink_to(real)

    atomic_json_write(link, {"x": 1})

    import stat as _stat
    mode = _stat.S_IMODE(real.stat().st_mode)
    assert mode == 0o644, f"permissions drifted after symlinked write: {oct(mode)}"


# ─── Ownership preservation (root-run secret write must not flip owner) ────


def test_atomic_replace_restores_owner_when_writer_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A privileged writer must not silently re-own the target.

    Regression for the takyon-dashboard 502: ``takyon secret set`` run as
    root rewrote ``/opt/takyon/.takyon/.env`` via ``_save_env_value_direct``
    → ``atomic_replace``; the rename installed a root-owned inode and the
    ``takyon`` service user could no longer read the file. ``atomic_replace``
    must restore the target's original uid/gid after the rename.
    """
    if not hasattr(os, "chown"):
        pytest.skip("POSIX-only")

    import utils

    target = tmp_path / ".env"
    target.write_text("OLD=1\n", encoding="utf-8")
    tmp = _write_tmp(tmp_path, "NEW=2\n")

    prev_owner = (995, 987)  # service user before the write

    class _FakeStat:
        def __init__(self, uid: int, gid: int) -> None:
            self.st_uid = uid
            self.st_gid = gid
            self.st_mode = 0o100600

    calls = {"stat": 0}
    real_target = os.path.realpath(str(target))

    def fake_stat(p, *a, **k):
        # Pre-replace stat reports the service user; post-replace reports the
        # root inode that the rename just installed.
        if calls["stat"] == 0:
            calls["stat"] += 1
            return _FakeStat(*prev_owner)
        return _FakeStat(0, 0)

    chown_calls: list = []
    monkeypatch.setattr(utils.os, "stat", fake_stat)
    monkeypatch.setattr(utils.os, "chown", lambda p, u, g: chown_calls.append((str(p), u, g)))

    utils.atomic_replace(tmp, target)

    assert chown_calls == [(real_target, 995, 987)]
    assert target.read_text(encoding="utf-8") == "NEW=2\n"


def test_atomic_replace_no_chown_when_owner_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common case (caller owns the file) must not call chown at all —
    otherwise an unprivileged user would hit a needless PermissionError."""
    if not hasattr(os, "chown"):
        pytest.skip("POSIX-only")

    import utils

    target = tmp_path / "plain.txt"
    target.write_text("old\n", encoding="utf-8")
    tmp = _write_tmp(tmp_path, "new\n")

    chown_calls: list = []
    monkeypatch.setattr(utils.os, "chown", lambda p, u, g: chown_calls.append((str(p), u, g)))

    utils.atomic_replace(tmp, target)

    assert chown_calls == []
    assert target.read_text(encoding="utf-8") == "new\n"


# ─── Broken-symlink edge case ─────────────────────────────────────────────


def test_atomic_replace_broken_symlink_creates_target(tmp_path: Path) -> None:
    """A symlink pointing at a missing file: the write should create the
    real target (resolving via realpath) rather than leaving the dangling
    link in place as a regular file.
    """
    missing = tmp_path / "does_not_exist_yet.yaml"
    link = tmp_path / "link.yaml"
    link.symlink_to(missing)
    assert link.is_symlink()
    assert not missing.exists()

    tmp = _write_tmp(tmp_path, "created-through-link\n")
    atomic_replace(tmp, link)

    assert link.is_symlink(), "symlink must be preserved"
    assert missing.exists(), "real target should now exist"
    assert missing.read_text(encoding="utf-8") == "created-through-link\n"
