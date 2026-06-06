#!/usr/bin/env python3
"""Direct local entrypoint for the Takyon Claude worker handoff.

This starts at ``business_claude_agent_task`` instead of the CEO/bootstrap
path, while preserving the same worker shape Takyon uses for delegated
``product/site`` work.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from plugins.takyon.core import handle_business_claude_agent_task  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="takyon-handoff",
        description=(
            "Directly invoke the Takyon Claude worker handoff for one existing "
            "business workspace."
        ),
    )
    parser.add_argument("business", help="Business slug")
    parser.add_argument("instruction", help="Worker instruction")
    parser.add_argument(
        "--workspace",
        default="product/site",
        help="Business-relative workspace. Default: product/site",
    )
    parser.add_argument(
        "--guidance-skill",
        dest="guidance_skills",
        action="append",
        default=[],
        help="Optional guidance skill to distill into the worker prompt. Repeatable.",
    )
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--effort",
        choices=("low", "medium", "high"),
        default="",
    )
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--timeout-ms", type=int, default=None)
    parser.add_argument("--refresh-timeout-seconds", type=int, default=None)
    parser.add_argument(
        "--no-refresh-surface",
        action="store_true",
        help="Skip the product surface refresh/publish pass after the worker run.",
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Skip package install during the refresh/build pass.",
    )
    parser.add_argument(
        "--reason",
        default="direct local handoff",
        help="Audit reason recorded with the worker run.",
    )
    parser.add_argument(
        "--actor",
        default="operator",
        help="Audit actor recorded with the worker run. Default: operator",
    )
    parser.add_argument(
        "--idempotency-key",
        default="",
        help="Optional explicit idempotency key. Defaults to a generated handoff key.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the raw tool JSON payload.",
    )
    return parser


def _format_summary(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(
        f"business:{payload.get('business') or '?'} "
        f"workspace:{payload.get('workspace') or '?'}"
    )
    lines.append(
        f"success:{bool(payload.get('success'))} "
        f"blocked:{bool(payload.get('blocked'))}"
    )
    model = str(payload.get("model") or "").strip()
    if model:
        lines.append(f"model:{model}")
    summary = str(payload.get("summary") or "").strip()
    if summary:
        lines.extend(["", summary])
    surface_refresh = payload.get("surface_refresh")
    if isinstance(surface_refresh, dict):
        status = str(surface_refresh.get("status") or "").strip()
        blocker = str(
            surface_refresh.get("blocker")
            or surface_refresh.get("exact_blocker")
            or ""
        ).strip()
        if status or blocker:
            lines.append("")
            lines.append(f"surface_refresh:{status or 'unknown'}")
            if blocker:
                lines.append(f"blocker:{blocker}")
    error = str(payload.get("error") or "").strip()
    if error:
        lines.append("")
        lines.append(f"error:{error}")
    return "\n".join(lines).rstrip()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    payload = {
        "business": args.business,
        "workspace": args.workspace,
        "instruction": args.instruction,
        "guidance_skills": args.guidance_skills,
        "refresh_surface": not args.no_refresh_surface,
        "install": not args.no_install,
        "reason": args.reason,
        "actor": args.actor,
        "idempotency_key": (
            args.idempotency_key
            or f"direct-handoff:{args.business}:{uuid.uuid4().hex}"
        ),
    }
    if args.budget_usd is not None:
        payload["budget_usd"] = args.budget_usd
    if args.model:
        payload["model"] = args.model
    if args.effort:
        payload["effort"] = args.effort
    if args.max_turns is not None:
        payload["max_turns"] = args.max_turns
    if args.timeout_ms is not None:
        payload["timeout_ms"] = args.timeout_ms
    if args.refresh_timeout_seconds is not None:
        payload["refresh_timeout_seconds"] = args.refresh_timeout_seconds

    raw = handle_business_claude_agent_task(payload)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        print(raw)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(_format_summary(result))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
