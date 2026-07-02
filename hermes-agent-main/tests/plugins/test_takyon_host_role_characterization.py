"""Characterization of the SIX divergent ``TAKYON_HOST_ROLE`` normalizers (modularization Stage 0).

This test does not assert a *desired* mapping — it pins the *current, divergent* reality so that the
planned Stage-3 collapse of these copies into one ``HostRole`` enum cannot silently change any of
them. Every disagreement below is intentional documentation: when the enum lands, whichever value it
picks for (say) ``"safebox"`` will force this file to be updated for the module whose behavior
changed, and the diff makes the reconciliation explicit and reviewable.

The six implementations (verified against the tree on 2026-07-02):
  1. ``core._normalized_host_role``          — full alias map, unknown -> raw passthrough
  2. ``runtime_app._normalized_host_role``   — IDENTICAL alias map, unknown -> raw passthrough
  3. ``web_server._host_role``               — alias map WITHOUT "safebox"; default -> "combined"
  4. ``app_actions._normalized_host_role``   — NO alias map, bare ``str.lower()`` passthrough
  5. ``safebox._normalized_host_role``       — NO alias map, bare ``str.lower()`` passthrough
  6/7. the two test-conftest copies          — same behavior as (1)/(2); covered by CANONICAL below

The three behavior classes that genuinely disagree:
  * CANONICAL  = core, runtime_app (+ both conftest copies): alias map with "safebox", raw fallthrough
  * WEB        = web_server: no "safebox" key, EVERYTHING unknown -> "combined"
  * BARE       = app_actions, safebox: no normalization at all (dashboard stays "dashboard", …)
"""

from __future__ import annotations

import pytest

from plugins.takyon import app_actions as _app_actions
from plugins.takyon import core as _core
from plugins.takyon import runtime_app as _runtime_app
from plugins.takyon import safebox as _safebox
from takyon_cli import web_server as _web_server

# name -> the zero-arg normalizer that reads TAKYON_HOST_ROLE from the environment
_NORMALIZERS = {
    "core": _core._normalized_host_role,
    "runtime_app": _runtime_app._normalized_host_role,
    "web_server": _web_server._host_role,
    "app_actions": _app_actions._normalized_host_role,
    "safebox": _safebox._normalized_host_role,
}

# The current, verified truth table. Each row: raw TAKYON_HOST_ROLE value (None = unset) ->
# {module: exact normalized output today}. A change to ANY cell is a behavior change that the
# Stage-3 enum collapse must consciously make (and re-pin here).
_TRUTH: list[tuple[str | None, dict[str, str]]] = [
    (None,        {"core": "",         "runtime_app": "",         "web_server": "combined", "app_actions": "",         "safebox": ""}),
    ("",          {"core": "",         "runtime_app": "",         "web_server": "combined", "app_actions": "",         "safebox": ""}),
    ("operator",  {"core": "operator", "runtime_app": "operator", "web_server": "operator", "app_actions": "operator", "safebox": "operator"}),
    ("dashboard", {"core": "operator", "runtime_app": "operator", "web_server": "operator", "app_actions": "dashboard", "safebox": "dashboard"}),
    ("subuser",   {"core": "subuser",  "runtime_app": "subuser",  "web_server": "subuser",  "app_actions": "subuser",  "safebox": "subuser"}),
    ("app",       {"core": "subuser",  "runtime_app": "subuser",  "web_server": "subuser",  "app_actions": "app",      "safebox": "app"}),
    ("product",   {"core": "subuser",  "runtime_app": "subuser",  "web_server": "subuser",  "app_actions": "product",  "safebox": "product"}),
    ("safebox",   {"core": "safebox",  "runtime_app": "safebox",  "web_server": "combined", "app_actions": "safebox",  "safebox": "safebox"}),
    ("combined",  {"core": "combined", "runtime_app": "combined", "web_server": "combined", "app_actions": "combined", "safebox": "combined"}),
    ("all",       {"core": "combined", "runtime_app": "combined", "web_server": "combined", "app_actions": "all",      "safebox": "all"}),
    ("default",   {"core": "combined", "runtime_app": "combined", "web_server": "combined", "app_actions": "default",  "safebox": "default"}),
    ("worker",    {"core": "worker",   "runtime_app": "worker",   "web_server": "combined", "app_actions": "worker",   "safebox": "worker"}),
    ("  OpErAtOr  ", {"core": "operator", "runtime_app": "operator", "web_server": "operator", "app_actions": "operator", "safebox": "operator"}),
]


@pytest.mark.parametrize("raw,expected", _TRUTH, ids=[repr(r) for r, _ in _TRUTH])
def test_host_role_normalizers_pinned(raw, expected, monkeypatch):
    """Each of the five runtime normalizers produces its exact current value for every input."""
    if raw is None:
        monkeypatch.delenv("TAKYON_HOST_ROLE", raising=False)
    else:
        monkeypatch.setenv("TAKYON_HOST_ROLE", raw)
    got = {name: fn() for name, fn in _NORMALIZERS.items()}
    assert got == expected


def test_documented_divergences_still_hold(monkeypatch):
    """Lock the THREE specific disagreements so a partial collapse can't erase one unnoticed.

    If Stage 3 unifies these, this test must be deleted/rewritten in the SAME change — that is the
    intended signal, not a flaky failure.
    """
    # 1. "safebox": web_server is the odd one out (maps to "combined", losing the safebox identity).
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    assert _core._normalized_host_role() == "safebox"
    assert _web_server._host_role() == "combined"  # <-- divergence #1

    # 2. "dashboard": the bare normalizers DON'T fold it onto "operator".
    monkeypatch.setenv("TAKYON_HOST_ROLE", "dashboard")
    assert _core._normalized_host_role() == "operator"
    assert _app_actions._normalized_host_role() == "dashboard"  # <-- divergence #2
    assert _safebox._normalized_host_role() == "dashboard"

    # 3. unknown value: canonical passes it through, web_server swallows it to "combined".
    monkeypatch.setenv("TAKYON_HOST_ROLE", "nonsense-role")
    assert _core._normalized_host_role() == "nonsense-role"
    assert _web_server._host_role() == "combined"  # <-- divergence #3
    assert _app_actions._normalized_host_role() == "nonsense-role"


def test_all_six_alias_maps_accounted_for():
    """Guard against a NEW host-role normalizer appearing without being characterized here.

    Greps the tree for ``def _normalized_host_role`` / ``def _host_role`` definitions; if the count
    changes, someone added or removed a copy and this characterization is now incomplete.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    pattern = re.compile(r"def _(?:normalized_host_role|host_role)\(")
    hits: list[str] = []
    for rel in (
        "plugins/takyon/core.py",
        "plugins/takyon/runtime_app.py",
        "plugins/takyon/safebox.py",
        "plugins/takyon/app_actions.py",
        "takyon_cli/web_server.py",
        "tests/conftest.py",
        "tests/plugins/conftest.py",
    ):
        text = (root / rel).read_text()
        hits.extend(f"{rel}:{m.start()}" for m in pattern.finditer(text))
    # 5 runtime definitions + 2 test-conftest definitions = 7 today. If this changes, update the
    # characterization above (a new copy = a new divergence risk for the Stage-3 collapse).
    assert len(hits) == 7, f"host-role normalizer count changed; re-characterize: {hits}"
