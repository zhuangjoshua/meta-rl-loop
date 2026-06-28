"""Takyon CEO operator plugin.

The plugin intentionally stays small: Takyon does the reasoning, while this
module provides scoped durable state, safety checks, and a CLI entrypoint.
"""

from __future__ import annotations

from . import web_spend
from .core import (
    TAKYON_TOOL_DEFINITIONS,
    takyon_toolset_name,
)
from .cli import register_cli, takyon_command, takyon_slash_command


def register(ctx) -> None:
    for tool in TAKYON_TOOL_DEFINITIONS:
        ctx.register_tool(
            name=tool["name"],
            toolset=takyon_toolset_name(tool["name"]),
            schema=tool["schema"],
            handler=tool["handler"],
            description=tool["description"],
        )
    # Fail-closed metering for the operator agent's paid web tools (web_search/web_extract/
    # web_crawl) and their summarizer LLM. The spend boundary lives in tools/web_tools.py, where the
    # ACTUAL provider is known: free backends never reserve; a paid backend reserves before egress
    # and settles the real cost or releases the hold. This installs the operator-budget
    # implementation of that seam (agent/web_spend_meter.py) — no pre/post tool-call hook guesswork.
    web_spend.register()
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
