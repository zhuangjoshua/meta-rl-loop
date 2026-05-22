"""Regression tests for _apply_profile_override TAKYON_HOME guard (issue #22502).

When TAKYON_HOME is set to the takyon root (e.g. systemd hardcodes
TAKYON_HOME=/root/.takyon), _apply_profile_override must still read
active_profile and update TAKYON_HOME to the profile directory.

When TAKYON_HOME is already a profile directory (.../profiles/<name>),
_apply_profile_override must trust it and return without re-reading
active_profile (child-process inheritance contract).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _run_apply_profile_override(
    tmp_path, monkeypatch, *, takyon_home: str | None, active_profile: str | None,
    argv: list[str] | None = None,
):
    """Run _apply_profile_override in isolation.

    Returns the value of os.environ["TAKYON_HOME"] after the call,
    or None if unset.
    """
    takyon_root = tmp_path / ".takyon"
    takyon_root.mkdir(parents=True, exist_ok=True)

    if active_profile is not None:
        (takyon_root / "active_profile").write_text(active_profile)

    if active_profile and active_profile != "default":
        (takyon_root / "profiles" / active_profile).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if takyon_home is not None:
        monkeypatch.setenv("TAKYON_HOME", takyon_home)
    else:
        monkeypatch.delenv("TAKYON_HOME", raising=False)

    monkeypatch.setattr(sys, "argv", argv or ["takyon", "gateway", "start"])

    from takyon_cli.main import _apply_profile_override
    _apply_profile_override()

    return os.environ.get("TAKYON_HOME")


class TestApplyProfileOverrideTakyonHomeGuard:
    """Regression guard for issue #22502.

    Verifies that TAKYON_HOME pointing to the takyon root does NOT suppress
    the active_profile check, while TAKYON_HOME already pointing to a
    profile directory IS trusted as-is.
    """

    def test_takyon_home_at_root_with_active_profile_is_redirected(
        self, tmp_path, monkeypatch
    ):
        """TAKYON_HOME=/root/.takyon + active_profile=coder must redirect
        TAKYON_HOME to .../profiles/coder.

        Bug scenario from #22502: systemd sets TAKYON_HOME to the takyon root
        and the user switches to a profile via `takyon profile use`.
        Before the fix, the guard returned early and active_profile was ignored.
        """
        takyon_root = tmp_path / ".takyon"
        takyon_root.mkdir(parents=True, exist_ok=True)

        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            takyon_home=str(takyon_root),
            active_profile="coder",
        )

        assert result is not None, "TAKYON_HOME must be set after profile redirect"
        assert "profiles" in result, (
            f"Expected TAKYON_HOME to point into profiles/ dir, got: {result!r}"
        )
        assert result.endswith("coder"), (
            f"Expected TAKYON_HOME to end with 'coder', got: {result!r}"
        )

    def test_takyon_home_already_profile_dir_is_trusted(self, tmp_path, monkeypatch):
        """TAKYON_HOME=.../profiles/coder must not be overridden even when
        active_profile says something different.

        Preserves the child-process inheritance contract: a subprocess spawned
        with TAKYON_HOME already set to a specific profile must stay in that
        profile.
        """
        takyon_root = tmp_path / ".takyon"
        profile_dir = takyon_root / "profiles" / "coder"
        profile_dir.mkdir(parents=True, exist_ok=True)

        (takyon_root / "active_profile").write_text("other")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("TAKYON_HOME", str(profile_dir))
        monkeypatch.setattr(sys, "argv", ["takyon", "gateway", "start"])

        from takyon_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("TAKYON_HOME") == str(profile_dir), (
            "TAKYON_HOME must remain unchanged when already pointing to a profile dir"
        )

    def test_takyon_home_unset_reads_active_profile(self, tmp_path, monkeypatch):
        """Classic case: TAKYON_HOME unset + active_profile=coder must set
        TAKYON_HOME to the profile directory (existing behaviour must not regress).
        """
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            takyon_home=None,
            active_profile="coder",
        )

        assert result is not None
        assert "coder" in result

    def test_takyon_home_unset_default_profile_no_redirect(self, tmp_path, monkeypatch):
        """active_profile=default must not redirect TAKYON_HOME."""
        takyon_root = tmp_path / ".takyon"
        takyon_root.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("TAKYON_HOME", raising=False)
        monkeypatch.setattr(sys, "argv", ["takyon", "gateway", "start"])
        (takyon_root / "active_profile").write_text("default")

        from takyon_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("TAKYON_HOME") is None
