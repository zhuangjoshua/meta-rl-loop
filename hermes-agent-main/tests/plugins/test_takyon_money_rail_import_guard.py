"""Import-graph guard for the money rails (modularization Stage 0).

The money leaves — ``billing`` (control-plane spend), ``app_usage`` (subuser AI usage),
``business_credits`` (creative credits), and their shared ``ledger_gate`` authority boundary — are
the most correctness-critical, least-coupled code in the tree. Their isolation from the 35k-line
``core`` god-object is what makes them independently reviewable and safe to refactor around.

This guard uses AST (so it catches LAZY, in-function imports too, not just module-level ones) to
enforce two invariants that the whole modularization plan leans on:

  1. HARD RULE — no money rail may import ``core`` (directly or lazily), ever. A violation would
     re-couple money truth to everything and is the single failure this file exists to prevent.
  2. ALLOWLIST — each money rail's first-party dependency set is pinned. A NEW first-party import
     isn't necessarily wrong, but it must be a CONSCIOUS change: it fails here, forcing the author
     to widen the allowlist in the same diff (and a reviewer to see the new coupling).

Verified against the tree on 2026-07-02 (exhaustive AST walk, module-level + lazy):
  billing.py         -> {ledger_gate, safebox}
  app_usage.py       -> {runtime_app, app_identity, openmeter_backend}
  business_credits.py-> {ledger_gate, safebox}
  ledger_gate.py     -> {runtime_app}
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_PKG = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "takyon"

# The pinned, verified first-party dependency set per money rail. Keep tight: widening any of these
# is a real architectural decision that belongs in the same change as the new import.
_ALLOWED_FIRST_PARTY: dict[str, set[str]] = {
    "billing": {"ledger_gate", "safebox"},
    "app_usage": {"runtime_app", "app_identity", "openmeter_backend"},
    "business_credits": {"ledger_gate", "safebox"},
    "ledger_gate": {"runtime_app"},
}

# First-party = imported from this package, by either relative (``from . import x`` / ``from .x``)
# or absolute (``from plugins.takyon.x`` / ``from plugins.takyon import x``) form.
_FIRST_PARTY_ABS_PREFIXES = ("plugins.takyon", "plugins/takyon")


def _first_party_imports(module_name: str) -> set[str]:
    """Every first-party leaf module imported by ``module_name`` — module-level AND lazy."""
    tree = ast.parse((_PKG / f"{module_name}.py").read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level and node.level > 0:
                # relative: ``from . import safebox`` (mod == "") or ``from .runtime_app import x``
                if mod:
                    found.add(mod.split(".")[-1])
                else:
                    for alias in node.names:
                        found.add(alias.name.split(".")[-1])
            elif mod == "plugins.takyon":
                # ``from plugins.takyon import openmeter_backend`` -> the imported NAME is the module
                for alias in node.names:
                    found.add(alias.name.split(".")[-1])
            elif mod.startswith("plugins.takyon."):
                # ``from plugins.takyon.runtime_app import x`` -> the module leaf
                found.add(mod.split(".")[-1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("plugins.takyon"):
                    found.add(alias.name.split(".")[-1])
    return found


@pytest.mark.parametrize("module_name", sorted(_ALLOWED_FIRST_PARTY))
def test_money_rail_never_imports_core(module_name):
    """HARD RULE: the money rails must never couple to the core god-object."""
    imports = _first_party_imports(module_name)
    assert "core" not in imports, (
        f"{module_name}.py now imports `core` (directly or lazily). The money rails MUST stay "
        f"decoupled from the 35k-line core god-object — route the needed helper through "
        f"ledger_gate/runtime_app or a small leaf instead. Imports seen: {sorted(imports)}"
    )


@pytest.mark.parametrize("module_name", sorted(_ALLOWED_FIRST_PARTY))
def test_money_rail_first_party_imports_within_allowlist(module_name):
    """ALLOWLIST: no NEW first-party coupling slips in unreviewed."""
    imports = _first_party_imports(module_name)
    allowed = _ALLOWED_FIRST_PARTY[module_name]
    extra = imports - allowed
    assert not extra, (
        f"{module_name}.py grew NEW first-party import(s): {sorted(extra)}. If intentional, add them "
        f"to _ALLOWED_FIRST_PARTY in this test IN THE SAME CHANGE so the new coupling is reviewed. "
        f"Currently allowed: {sorted(allowed)}."
    )


def test_allowlist_covers_exactly_the_money_rails():
    """Guard against a money rail being added/renamed without a guard entry."""
    # The money rails are exactly these four leaves. If billing/usage/credits gain a sibling
    # (e.g. business_ad_spend graduates into a core money rail), it must be characterized here.
    for name in _ALLOWED_FIRST_PARTY:
        assert (_PKG / f"{name}.py").exists(), f"money rail {name}.py missing — update the guard"
