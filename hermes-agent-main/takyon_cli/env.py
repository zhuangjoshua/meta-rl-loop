"""CLI handlers for ``takyon env create|status|destroy <name>`` (modularization Stage 3b, UC3).

Thin shell over :class:`plugins.takyon.env_provisioner.EnvironmentProvisioner`. The command:

- refuses ``name == 'prod'`` (the provisioner also refuses it — belt and suspenders);
- prints one receipt line per step, with blocked steps naming the EXACT alias to deposit;
- exits non-zero when any step is blocked/errored, so a CI/deploy caller sees the fail-closed signal.
"""

from __future__ import annotations

import sys
from typing import Any

from takyon_cli.colors import Colors, color


def cmd_env(args: Any) -> int:
    """Dispatcher for ``takyon env <action> <name>``."""
    action = getattr(args, "env_action", None)
    if action not in ("create", "status", "destroy", "restart"):
        print("usage: takyon env {create|status|destroy|restart} <name> [--force]", file=sys.stderr)
        raise SystemExit(2)

    name = str(getattr(args, "env_name", "") or "").strip().lower()
    if not name:
        print("usage: takyon env {create|status|destroy|restart} <name>", file=sys.stderr)
        raise SystemExit(2)
    if name == "prod":
        print(
            f"  {color('✗', Colors.RED)} refusing `takyon env {action} prod` — this rail only stands "
            "up isolated twins, never prod.",
            file=sys.stderr,
        )
        return 2

    # Local import keeps CLI startup light and the provisioner inert until invoked.
    from plugins.takyon.core import load_takyon_env
    from plugins.takyon.env_provisioner import EnvironmentProvisioner, EnvironmentProvisionError

    load_takyon_env()

    try:
        provisioner = EnvironmentProvisioner(name)
    except EnvironmentProvisionError as exc:
        print(f"  {color('✗', Colors.RED)} {exc}", file=sys.stderr)
        return 1

    if action == "create":
        result = provisioner.create()
    elif action == "status":
        result = provisioner.status()
    elif action == "restart":
        # Drain-aware rolling restart of the env's replicas (the full-4b graceful-drain rail):
        # per replica remove-from-LB -> in-flight grace -> converge front + restart unit ->
        # local health verify -> re-add -> proven back in rotation. Zero requests lost on
        # planned restarts/deploys; fail-closed if the other replica cannot carry the traffic.
        result = provisioner.rolling_restart()
    else:
        result = provisioner.destroy(force=bool(getattr(args, "force", False)))

    print()
    print(color(f"◆ takyon env {action} {name}", Colors.CYAN, Colors.BOLD))
    print()
    for r in result.receipts:
        glyph, tone = _receipt_glyph(r.status)
        line = f"  {color(glyph, tone)} {r.resource}: {r.detail or r.status}"
        print(line)
        if r.deposit:
            print(f"      {color('→ deposit', Colors.YELLOW)} {r.deposit}")
    print()

    if result.blocked:
        deposits = sorted({r.deposit for r in result.blocked if r.deposit})
        print(color(
            f"blocked: {len(result.blocked)} step(s) need a credential deposit first: "
            + ", ".join(deposits),
            Colors.YELLOW,
        ))
        print()

    if action == "destroy" and result.ok:
        print(color("destroy complete — receipts recorded exactly what was removed.", Colors.DIM))
    if not result.ok:
        # Propagate the fail-closed signal as a non-zero exit so a CI/deploy caller (and the shell)
        # sees it — main() drops plain return values.
        raise SystemExit(1)
    return 0


def _receipt_glyph(status: str) -> tuple[str, str]:
    return {
        "created": ("✓", Colors.GREEN),
        "exists": ("✓", Colors.GREEN),
        "deleted": ("✓", Colors.GREEN),
        "disabled": ("·", Colors.DIM),
        "skipped": ("·", Colors.DIM),
        "blocked": ("⚠", Colors.YELLOW),
        "error": ("✗", Colors.RED),
    }.get(status, ("•", Colors.DIM))
