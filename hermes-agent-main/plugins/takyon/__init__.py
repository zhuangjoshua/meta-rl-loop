"""Takyon CEO operator plugin.

The plugin intentionally stays small: Takyon does the reasoning, while this
module provides scoped durable state, safety checks, and a CLI entrypoint.
"""

from __future__ import annotations

import re
from pathlib import Path

from .core import TAKYON_TOOL_DEFINITIONS
from .cli import register_cli, takyon_command, takyon_slash_command


def _skill_description(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r"^description:\s*(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip().strip('"') if match else ""


def _register_skills(ctx) -> None:
    skills_root = Path(__file__).parent / "skills"
    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        ctx.register_skill(
            name=skill_md.parent.name,
            path=skill_md,
            description=_skill_description(skill_md),
        )


def register(ctx) -> None:
    for tool in TAKYON_TOOL_DEFINITIONS:
        ctx.register_tool(
            name=tool["name"],
            toolset="takyon",
            schema=tool["schema"],
            handler=tool["handler"],
            description=tool["description"],
        )
    _register_skills(ctx)
    ctx.register_cli_command(
        name="takyon",
        help="Run the Takyon CEO operator",
        setup_fn=register_cli,
        handler_fn=takyon_command,
        description="Natural-language CEO control plus status/list/pause/kill/wakeup commands.",
    )
    ctx.register_command(
        "takyon",
        handler=lambda raw_args: takyon_slash_command(raw_args, ctx),
        description="Takyon CEO control and namespaced Takyon skill invocation.",
        args_hint="[command|skill] [args...]",
    )
