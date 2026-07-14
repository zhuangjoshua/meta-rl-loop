"""Regression tests for removed dead config keys.

This file guards against accidental re-introduction of config keys that were
documented or declared at some point but never actually wired up to read code.
Future dead-config regressions can accumulate here.
"""

import inspect


def test_legacy_delegation_removed_from_cli_config():
    """The Claude Agent SDK primary session does not expose delegation."""
    from cli import load_cli_config

    source = inspect.getsource(load_cli_config)
    assert '"delegation"' not in source
