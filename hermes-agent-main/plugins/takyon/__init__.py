"""Takyon CEO operator plugin.

The plugin intentionally stays small: Takyon does the reasoning, while this
module provides scoped durable state, safety checks, and a CLI entrypoint.
"""

from __future__ import annotations

from . import cost_events, web_spend
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
            # Every Takyon tool call appends a tool_call row to operator_cost_events
            # (migration 0070) — the per-tool-call slice of the cost/log debugging ledger.
            # Observability only: the wrapper re-raises untouched and swallows its own failures.
            handler=cost_events.wrap_business_tool_handler(tool["name"], tool["handler"]),
            description=tool["description"],
        )
    # One llm_call row per Takyon-agent API call (model, token buckets, exact priced cost) —
    # the generic loop already fires post_api_request with the canonical usage summary.
    ctx.register_hook("post_api_request", cost_events.post_api_request_hook)
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
