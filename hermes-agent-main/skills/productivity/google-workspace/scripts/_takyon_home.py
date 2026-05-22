"""Resolve TAKYON_HOME for standalone skill scripts.

Skill scripts may run outside the Takyon process (e.g. system Python,
nix env, CI) where ``takyon_constants`` is not importable.  This module
provides the same ``get_takyon_home()`` and ``display_takyon_home()``
contracts as ``takyon_constants`` without requiring it on ``sys.path``.

When ``takyon_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``takyon_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``TAKYON_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from takyon_constants import display_takyon_home as display_takyon_home
    from takyon_constants import get_takyon_home as get_takyon_home
except (ModuleNotFoundError, ImportError):

    def get_takyon_home() -> Path:
        """Return the Takyon home directory (default: ~/.takyon).

        Mirrors ``takyon_constants.get_takyon_home()``."""
        val = os.environ.get("TAKYON_HOME", "").strip()
        return Path(val) if val else Path.home() / ".takyon"

    def display_takyon_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``takyon_constants.display_takyon_home()``."""
        home = get_takyon_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
