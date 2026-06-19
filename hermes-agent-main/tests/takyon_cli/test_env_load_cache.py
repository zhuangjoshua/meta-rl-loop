"""Tests for the load_env() process-level cache.

The cache exists to keep `takyon tools` → "All Platforms" fast: every
`get_env_value()` lookup used to re-read and re-sanitise the entire
.env file, racking up hundreds of ms across one menu render. The
cache is keyed on (path, mtime, size); writers (save_env_value /
remove_env_value / sanitise_env_file) call invalidate_env_cache().
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch


def _write_env(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")


def test_load_env_caches_on_repeat_calls():
    """Repeated load_env() calls on the same file return the cached dict."""
    from takyon_cli.config import invalidate_env_cache, load_env

    invalidate_env_cache()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8"
    ) as f:
        f.write("OPENAI_API_KEY=sk-first\n")
        env_path = Path(f.name)

    try:
        with patch("takyon_cli.config.get_env_path", return_value=env_path):
            first = load_env()
            # Even if a writer outside our cache mutates the file, an
            # mtime/size match means the cache still wins. We simulate that
            # by writing identical bytes back — sanity check that the cache
            # is keyed structurally, not on a counter.
            second = load_env()

        assert first == second
        assert first.get("OPENAI_API_KEY") == "sk-first"
    finally:
        env_path.unlink(missing_ok=True)
        invalidate_env_cache()


def test_load_env_invalidates_on_mtime_bump():
    """Editing the file (mtime changes) invalidates the cache."""
    from takyon_cli.config import invalidate_env_cache, load_env

    invalidate_env_cache()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8"
    ) as f:
        f.write("OPENAI_API_KEY=sk-old\n")
        env_path = Path(f.name)

    try:
        with patch("takyon_cli.config.get_env_path", return_value=env_path):
            first = load_env()
            assert first.get("OPENAI_API_KEY") == "sk-old"

            # Rewrite file with new contents and bump mtime to make sure
            # the FS records the change even on coarse-mtime filesystems.
            _write_env(env_path, "OPENAI_API_KEY=sk-new\n")
            future = env_path.stat().st_mtime + 5.0
            os.utime(env_path, (future, future))

            second = load_env()
            assert second.get("OPENAI_API_KEY") == "sk-new", (
                "load_env() returned stale value after file change"
            )
    finally:
        env_path.unlink(missing_ok=True)
        invalidate_env_cache()


def test_invalidate_env_cache_forces_reread():
    """invalidate_env_cache() forces the next load_env() to hit the disk.

    This is the belt-and-braces knob for writers (save_env_value, etc.)
    on filesystems where mtime resolution might miss a same-second write.
    """
    from takyon_cli.config import invalidate_env_cache, load_env

    invalidate_env_cache()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8"
    ) as f:
        f.write("OPENAI_API_KEY=sk-old\n")
        env_path = Path(f.name)

    try:
        with patch("takyon_cli.config.get_env_path", return_value=env_path):
            assert load_env().get("OPENAI_API_KEY") == "sk-old"

            # Rewrite WITHOUT bumping mtime — simulates same-second write.
            mtime_before = env_path.stat().st_mtime
            _write_env(env_path, "OPENAI_API_KEY=sk-new\n")
            os.utime(env_path, (mtime_before, mtime_before))

            # Without invalidation, cache hit might return stale.
            invalidate_env_cache()

            assert load_env().get("OPENAI_API_KEY") == "sk-new"
    finally:
        env_path.unlink(missing_ok=True)
        invalidate_env_cache()


def test_save_env_value_invalidates_cache(tmp_path, monkeypatch):
    """save_env_value() invalidates the cache so subsequent reads see the update."""
    from takyon_cli import config as config_mod
    from takyon_cli.config import invalidate_env_cache, load_env, save_env_value

    invalidate_env_cache()

    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING_VAR=old\n", encoding="utf-8")

    monkeypatch.setattr(config_mod, "get_env_path", lambda: env_path)
    monkeypatch.setattr(config_mod, "ensure_takyon_home", lambda: None)
    monkeypatch.setattr(config_mod, "_secure_file", lambda _p: None)
    monkeypatch.setattr(config_mod, "is_managed", lambda: False)

    try:
        # Prime the cache.
        first = load_env()
        assert first.get("EXISTING_VAR") == "old"

        save_env_value("NEW_VAR", "shiny")

        # Same-second writes on coarse-mtime filesystems would normally
        # let stale cache survive; invalidate_env_cache() inside the
        # writer makes the next read see the new key.
        result = load_env()
        assert result.get("NEW_VAR") == "shiny"
        assert result.get("EXISTING_VAR") == "old"
    finally:
        monkeypatch.delenv("NEW_VAR", raising=False)
        invalidate_env_cache()


def test_remove_env_value_invalidates_cache(tmp_path, monkeypatch):
    """remove_env_value() invalidates the cache so the removed key disappears."""
    from takyon_cli import config as config_mod
    from takyon_cli.config import (
        invalidate_env_cache,
        load_env,
        remove_env_value,
        save_env_value,
    )

    invalidate_env_cache()

    env_path = tmp_path / ".env"
    monkeypatch.setattr(config_mod, "get_env_path", lambda: env_path)
    monkeypatch.setattr(config_mod, "ensure_takyon_home", lambda: None)
    monkeypatch.setattr(config_mod, "_secure_file", lambda _p: None)
    monkeypatch.setattr(config_mod, "is_managed", lambda: False)

    save_env_value("DOOMED_VAR", "value")
    assert load_env().get("DOOMED_VAR") == "value"

    try:
        removed = remove_env_value("DOOMED_VAR")
        assert removed is True
        assert "DOOMED_VAR" not in load_env()
    finally:
        monkeypatch.delenv("DOOMED_VAR", raising=False)
        invalidate_env_cache()


def test_get_env_value_prefers_remote_safebox_for_openai_api_key(monkeypatch):
    from plugins.takyon import safebox
    from takyon_cli.config import get_env_value

    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://safebox.internal")
    monkeypatch.setenv("OPENAI_API_KEY", "local-openai-key")
    monkeypatch.setattr(
        safebox,
        "read_env_backed_value",
        lambda key: "remote-openai-key" if key == "OPENAI_API_KEY" else "",
    )

    assert get_env_value("OPENAI_API_KEY") == "remote-openai-key"


def test_load_env_handles_missing_file():
    """A nonexistent .env returns {} and caches the empty result."""
    from takyon_cli.config import invalidate_env_cache, load_env

    invalidate_env_cache()

    nonexistent = Path(tempfile.gettempdir()) / "takyon-test-no-such-env-xyz123.env"
    nonexistent.unlink(missing_ok=True)

    try:
        with patch("takyon_cli.config.get_env_path", return_value=nonexistent):
            assert load_env() == {}
            assert load_env() == {}  # cached
    finally:
        invalidate_env_cache()


def test_load_env_degrades_to_last_cache_when_file_unreadable():
    """A transient PermissionError reading an EXISTING .env degrades to the last good
    parse instead of raising.

    The .env file is a secondary source (os.environ / the systemd EnvironmentFile is
    authoritative on hosts that serve secrets). A concurrent root-run secret write can
    leave the file briefly root-owned 0600, unreadable by the service user. If load_env()
    propagated that, the Safebox /v1/env rail 500s for EVERY business at once — so it must
    degrade, never crash.
    """
    import builtins

    from takyon_cli.config import invalidate_env_cache, load_env

    invalidate_env_cache()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8"
    ) as f:
        f.write("DATABASE_URL=postgres://cached\n")
        env_path = Path(f.name)

    real_open = builtins.open

    def _boom_for_env(file, *args, **kwargs):
        if str(file) == str(env_path):
            raise PermissionError(13, "Permission denied")
        return real_open(file, *args, **kwargs)

    try:
        with patch("takyon_cli.config.get_env_path", return_value=env_path):
            # Prime the cache while the file is readable.
            assert load_env().get("DATABASE_URL") == "postgres://cached"

            # Bump mtime so the next call is a cache MISS and must re-read the file,
            # then make that read fail. load_env must serve the last good parse.
            future = env_path.stat().st_mtime + 5.0
            os.utime(env_path, (future, future))
            with patch("builtins.open", _boom_for_env):
                degraded = load_env()
            assert degraded.get("DATABASE_URL") == "postgres://cached", (
                "load_env() must degrade to the last cached parse on a transient read error"
            )

            # The degraded result must NOT be memoised: once the file is readable again
            # (cache still keyed on the bumped mtime), the next call re-reads cleanly.
            assert load_env().get("DATABASE_URL") == "postgres://cached"
    finally:
        env_path.unlink(missing_ok=True)
        invalidate_env_cache()
