"""Layering guard for the worker plane (modularization Stage 1).

The backend job handlers (``worker.py``) used to lazily import turn-configuration and
progress helpers from ``cli.py`` — the interactive shell module — a worker→UI layering
inversion (plan §1.5). Stage 1 moved those helpers verbatim into ``turn_runtime.py``
(a neutral leaf both planes share; ``cli.py`` re-exports them for shell callers/tests).

This guard pins the inversion fixed, in the same AST style as the money-rail guard
(catching LAZY in-function imports too):

  1. HARD RULE — ``worker.py`` must never import ``cli`` again, by any form.
  2. HARD RULE — ``turn_runtime.py`` must never import ``cli`` (that would re-create the
     cycle through the back door) and must never import ``worker`` (it is a shared leaf).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_PKG = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "takyon"

# module under guard -> first-party modules it must NEVER import
_FORBIDDEN: dict[str, set[str]] = {
    "worker": {"cli"},
    "worker_pool": {"cli"},
    "turn_runtime": {"cli", "worker"},
    # claim_scope is a queue-plane leaf (Stage 2): jobs/worker_pool/cli all import it, so it
    # must never import back up the stack (or into the money rails).
    "claim_scope": {"cli", "worker", "worker_pool", "core", "jobs", "billing"},
}


def _first_party_imports(module_name: str) -> set[str]:
    """Every first-party leaf module imported by ``module_name`` — module-level AND lazy."""
    tree = ast.parse((_PKG / f"{module_name}.py").read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level and node.level > 0:
                if mod:
                    found.add(mod.split(".")[-1])
                else:
                    for alias in node.names:
                        found.add(alias.name.split(".")[-1])
            elif mod == "plugins.takyon":
                for alias in node.names:
                    found.add(alias.name.split(".")[-1])
            elif mod.startswith("plugins.takyon."):
                found.add(mod.split(".")[-1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("plugins.takyon"):
                    found.add(alias.name.split(".")[-1])
    return found


@pytest.mark.parametrize("module_name", sorted(_FORBIDDEN))
def test_worker_plane_layering(module_name):
    imports = _first_party_imports(module_name)
    hit = imports & _FORBIDDEN[module_name]
    assert not hit, (
        f"{module_name}.py imports {sorted(hit)} (directly or lazily) — the worker plane must not "
        f"depend on the interactive shell module. Shared turn helpers belong in turn_runtime.py "
        f"(a neutral leaf); cli.py re-exports them for shell callers."
    )


def test_cli_still_reexports_turn_runtime_helpers():
    """The shim contract: every public helper moved to turn_runtime stays reachable via cli
    until each shell/test caller is proven moved (plan §5 shared-tree discipline)."""
    import plugins.takyon.cli as cli
    import plugins.takyon.turn_runtime as tr

    for name in (
        "_read_model_config",
        "_require_agent_model_config",
        "_takyon_reasoning_config",
        "_reasoning_progress_callback",
        "_tool_progress_lines",
        "_business_workspace_execution_context",
        "_ceo_bootstrap_turn_config",
        "_business_bootstrap_instruction",
        "_load_ceo_prompt",
        "_bootstrap_goal_requests_product_workflow",
    ):
        assert getattr(cli, name) is getattr(tr, name), (
            f"cli.{name} is no longer the turn_runtime object — the re-export shim broke"
        )
