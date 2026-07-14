#!/usr/bin/env python3
"""Fail if operator-agent material is present on a sub-user runtime plane."""

from __future__ import annotations

import pathlib
import sys


FORBIDDEN_RUNTIME_PATHS = (
    ".claude",
    "skills",
    "plugins/takyon/bootstrap_phases.py",
    "plugins/takyon/claude_sdk_runtime.py",
    "plugins/takyon/claude_sdk_sessions.py",
    "scripts/build_approved_skills_manifest.py",
    "scripts/takyon-claude-agent-task.mjs",
    "scripts/takyon-claude-primary-entrypoint.mjs",
    "scripts/takyon-claude-primary-runtime.mjs",
)

FORBIDDEN_RUNTIME_GLOBS = (
    "plugins/takyon/claude_sdk_runtime*.pyc",
    "plugins/takyon/claude_sdk_sessions*.pyc",
    "plugins/takyon/bootstrap_phases*.pyc",
    "plugins/takyon/__pycache__/claude_sdk_runtime*.pyc",
    "plugins/takyon/__pycache__/claude_sdk_sessions*.pyc",
    "plugins/takyon/__pycache__/bootstrap_phases*.pyc",
    "scripts/build_approved_skills_manifest*.pyc",
    "scripts/__pycache__/build_approved_skills_manifest*.pyc",
)


def forbidden_material(
    runtime: pathlib.Path,
    home: pathlib.Path,
    *,
    verify_home: bool,
) -> list[pathlib.Path]:
    present = [
        runtime / relative
        for relative in FORBIDDEN_RUNTIME_PATHS
        if (runtime / relative).exists() or (runtime / relative).is_symlink()
    ]
    for pattern in FORBIDDEN_RUNTIME_GLOBS:
        present.extend(
            candidate
            for candidate in runtime.glob(pattern)
            if candidate.exists() or candidate.is_symlink()
        )
    present.extend(
        candidate
        for candidate in (runtime / "node_modules" / "@anthropic-ai").glob(
            "claude-agent-sdk*"
        )
        if candidate.exists() or candidate.is_symlink()
    )
    present.extend(
        candidate
        for candidate in (runtime / "node_modules" / ".bin").glob("claude*")
        if candidate.exists() or candidate.is_symlink()
    )
    if verify_home:
        for candidate in (
            home / "skills",
            home / "claude-agent-sdk",
            home / "runtime" / "claude-agent-sdk",
        ):
            if candidate.exists() or candidate.is_symlink():
                present.append(candidate)
    return present


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[3] not in {"0", "1"}:
        raise SystemExit(
            "usage: verify-subuser-runtime-surface.py RUNTIME_ROOT TAKYON_HOME VERIFY_HOME_0_OR_1"
        )
    runtime = pathlib.Path(argv[1])
    home = pathlib.Path(argv[2])
    if not runtime.is_dir() or runtime.is_symlink():
        raise SystemExit(f"sub-user runtime root is unavailable or symlinked: {runtime}")
    if argv[3] == "1" and (not home.is_dir() or home.is_symlink()):
        raise SystemExit(f"sub-user Takyon home is unavailable or symlinked: {home}")
    present = forbidden_material(
        runtime,
        home,
        verify_home=argv[3] == "1",
    )
    if present:
        raise SystemExit(
            "forbidden operator runtime material on sub-user plane: "
            + ", ".join(str(path) for path in present)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
