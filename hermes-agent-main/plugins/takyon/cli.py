"""Terminal entrypoint for the Takyon plugin."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .core import TakyonStore, _slugify, load_takyon_env
from .registry import TAKYON_CATEGORIES, TAKYON_PRIORITY_BANDS, TAKYON_SKILL_REGISTRY, business_registry_snapshot


_LOCAL_COMMANDS = {
    "businesses",
    "business",
    "list",
    "registry",
    "campaigns",
    "workspaces",
    "cron",
    "crons",
    "show",
    "wake",
    "pause",
    "resume",
    "kill",
    "init",
    "gc",
}


def register_cli(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help=(
            "Natural language command, or: businesses | campaigns <business> | show <business> [path] | "
            "registry [all|tools|skills] [category] [priority] | cron [list|tick] | wake <business> [schedule] | "
            "pause/resume/kill <scope> [reason] | gc [days] [confirm]"
        ),
    )
    parser.add_argument("--json", action="store_true", help="Print raw JSON")
    parser.add_argument("--model", default="", help="Optional model override for natural-language runs")
    parser.add_argument("--max-turns", type=int, default=30, help="Maximum agent loop iterations")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="takyon",
        description="Takyon terminal CEO operator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_takyon_help().replace("/takyon", "takyon"),
    )
    register_cli(parser)
    parser.set_defaults(func=takyon_command)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    takyon_command(args)


def _print(value: Any, *, raw_json: bool = False) -> None:
    if raw_json:
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return
    if isinstance(value, str):
        print(value)
        return
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _scope_for_business(slug: str) -> str:
    return f"business:{_slugify(slug)}"


def _control(store: TakyonStore, scope: str, state: str, reason: str) -> dict[str, Any]:
    return store.commit(
        scope=scope,
        operations=[{"action": "control.set", "scope": scope, "state": state, "reason": reason}],
        idempotency_key=f"operator-control:{scope}:{state}:{reason}",
        reason=reason,
        actor="operator",
    )


def _takyon_help() -> str:
    return """\
/takyon - Takyon CEO and skill namespace

Takyon control:
  /takyon businesses
  /takyon registry [all|tools|skills] [category] [priority]
  /takyon campaigns <business>
  /takyon cron [list|tick]
  /takyon show <business> [path]
  /takyon wake <business> [schedule]
  /takyon pause|resume|kill <scope> [reason]
  /takyon gc [days] [confirm]

Takyon CEO:
  /takyon <natural language operator command>

Takyon skills through Takyon:
  /takyon <skill-name> <instruction>
  /takyon skill <skill-name> <instruction>
"""


def _format_slash_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False)


def _resolve_skill_reference(name: str) -> tuple[str, str] | None:
    clean = str(name or "").strip().lstrip("/")
    if not clean:
        return None
    key = f"/{clean}"
    try:
        from agent.skill_commands import get_skill_commands

        if key in get_skill_commands():
            return ("slash", key)
    except Exception:
        pass

    for item in TAKYON_SKILL_REGISTRY:
        if clean in {item["name"], item["skill"]}:
            return ("plugin", item["skill"])
    return None


def _plugin_skill_invocation_message(skill_ref: str, instruction: str) -> str | None:
    try:
        from tools.skills_tool import skill_view

        loaded = json.loads(skill_view(skill_ref))
    except Exception as exc:
        raise RuntimeError(f"could not load {skill_ref}: {exc}") from exc
    if not loaded.get("success") or not loaded.get("content"):
        return None
    skill_name = str(loaded.get("name") or skill_ref)
    content = str(loaded["content"])
    activation_note = (
        f'[IMPORTANT: The user has invoked the "{skill_name}" skill through /takyon, '
        "indicating they want you to follow its instructions. The full skill content is loaded below.]"
    )
    return (
        f"{activation_note}\n\n"
        f"<skill name=\"{skill_name}\">\n{content}\n</skill>\n\n"
        f"User instruction:\n{instruction or '(no extra instruction)'}"
    )


def _queue_skill_invocation(ctx: Any, skill_kind: str, skill_ref: str, instruction: str) -> str:
    try:
        if skill_kind == "slash":
            from agent.skill_commands import build_skill_invocation_message

            msg = build_skill_invocation_message(
                skill_ref,
                instruction,
                runtime_note="Invoked through the /takyon skill namespace.",
            )
        else:
            msg = _plugin_skill_invocation_message(skill_ref, instruction)
    except Exception as exc:
        return f"Takyon skill error: {exc}"
    if not msg:
        return f"Takyon could not load skill {skill_ref}."
    if ctx is not None and hasattr(ctx, "inject_message") and ctx.inject_message(msg):
        return f"Queued Takyon skill {skill_ref}."
    return (
        f"Takyon loaded {skill_ref}, but no active CLI conversation was available "
        "to receive it. Use the skill slash command directly in a running session."
    )


def _queue_ceo_invocation(ctx: Any, message: str) -> str:
    prompt = (
        "Takyon operator command:\n\n"
        f"{message}\n\n"
        "Use the Takyon CEO skill, business registry, and concrete business_* tools. Keep business state isolated."
    )
    if ctx is not None and hasattr(ctx, "inject_message") and ctx.inject_message(prompt):
        return "Queued Takyon CEO command."
    return _run_agent(
        message,
        model=os.getenv("TAKYON_MODEL", ""),
        max_turns=int(os.getenv("TAKYON_MAX_TURNS", "30") or 30),
    )


def _run_agent(message: str, *, model: str, max_turns: int) -> str:
    load_takyon_env()
    from run_agent import AIAgent

    skill = _load_ceo_skill()
    agent = AIAgent(
        model=model,
        max_iterations=max_turns,
        enabled_toolsets=["takyon", "web", "skills", "todo", "delegation"],
        disabled_toolsets=["cronjob", "messaging", "memory", "session_search", "terminal", "file", "browser", "code_execution"],
        ephemeral_system_prompt=skill,
        load_soul_identity=True,
        skip_memory=True,
        skip_context_files=True,
        platform="takyon",
        quiet_mode=False,
    )
    result = agent.run_conversation(
        "Takyon operator command:\n\n"
        f"{message}\n\n"
        "Use concrete business_* tools for all durable business state changes. "
        "Read business state before broad changes. Keep every write business-scoped. "
        "Do not claim a file write, budget allocation, job enqueue, agent record, or wakeup schedule succeeded "
        "unless the specific business tool returned success."
    )
    return str(result.get("final_response") or "")


def _load_ceo_skill() -> str:
    try:
        from tools.skills_tool import skill_view

        loaded = json.loads(skill_view("takyon:ceo"))
        if loaded.get("success") and loaded.get("content"):
            return str(loaded["content"])
    except Exception:
        pass
    skill_path = Path(__file__).parent / "skills" / "ceo" / "SKILL.md"
    return skill_path.read_text(encoding="utf-8")


def run_takyon_command(argv: list[str], *, raw_json: bool = False, model: str = "", max_turns: int = 30) -> Any:
    store = TakyonStore()

    if not argv:
        return store.read(scope="global", query="list_businesses")

    command = argv[0].lower()

    if command in {"help", "-h", "--help"}:
        return _takyon_help()

    if command in {"businesses", "business", "list"}:
        return store.read(scope="global", query="list_businesses")

    if command == "registry":
        kind = "all"
        category = None
        priority_band = None
        for token in argv[1:]:
            value = token.strip()
            if value in {"all", "tools", "skills"}:
                kind = value
            elif value in TAKYON_CATEGORIES:
                category = value
            elif value in TAKYON_PRIORITY_BANDS:
                priority_band = value
            else:
                raise SystemExit(
                    f"unknown registry filter {value!r}; use all|tools|skills, a category, or a priority band"
                )
        return business_registry_snapshot(kind=kind, category=category, priority_band=priority_band)

    if command in {"campaigns", "workspaces"}:
        if len(argv) < 2:
            raise SystemExit("usage: takyon campaigns <business>")
        slug = _slugify(argv[1])
        data = store.read(scope=_scope_for_business(slug), query="summary")
        workspaces = [
            item for item in data.get("workspaces", [])
            if str(item.get("path", "")).startswith("campaigns/")
        ]
        return {"success": True, "business": slug, "campaigns": workspaces}

    if command in {"cron", "crons"}:
        action = argv[1].lower() if len(argv) >= 2 else "list"
        if action in {"list", "jobs"}:
            from cron.jobs import list_jobs

            return {"success": True, "jobs": list_jobs(include_disabled=True)}
        if action in {"tick", "run", "run-due"}:
            from cron.scheduler import tick

            return {"success": True, "ran": tick()}
        raise SystemExit("usage: takyon cron [list|tick]")

    if command == "show":
        if len(argv) < 2:
            raise SystemExit("usage: takyon show <business> [path]")
        slug = _slugify(argv[1])
        if len(argv) >= 3:
            return store.read(scope=_scope_for_business(slug), query="read_file", path=argv[2])
        return store.read(scope=_scope_for_business(slug), query="summary")

    if command == "wake":
        if len(argv) < 2:
            raise SystemExit("usage: takyon wake <business> [schedule]")
        slug = _slugify(argv[1])
        schedule = " ".join(argv[2:]).strip() or "every 6h"
        return store.commit(
            scope=_scope_for_business(slug),
            operations=[{"action": "cron.ensure_ceo_wakeup", "business": slug, "schedule": schedule}],
            idempotency_key=f"operator-wake:{slug}:{schedule}",
            reason="operator requested CEO wake/sleep cron",
            actor="operator",
        )

    if command in {"pause", "resume", "kill"}:
        if len(argv) < 2:
            raise SystemExit(f"usage: takyon {command} <scope>|business <slug> [reason]")
        state = "active" if command == "resume" else ("killed" if command == "kill" else "paused")
        if argv[1] == "business" and len(argv) >= 3:
            scope = _scope_for_business(argv[2])
            reason = " ".join(argv[3:]).strip() or f"operator {command}"
        else:
            scope = argv[1]
            reason = " ".join(argv[2:]).strip() or f"operator {command}"
        return _control(store, scope, state, reason)

    if command == "init":
        if len(argv) < 2:
            raise SystemExit("usage: takyon init <business> [goal text]")
        slug = _slugify(argv[1])
        goal = " ".join(argv[2:]).strip()
        return store.commit(
            scope=_scope_for_business(slug),
            operations=[{"action": "business.upsert", "business": slug, "name": argv[1], "goal": goal}],
            idempotency_key=f"operator-init:{slug}:{goal}",
            reason="operator initialized business",
            actor="operator",
        )

    if command == "gc":
        days = int(argv[1]) if len(argv) >= 2 and argv[1].isdigit() else 90
        confirm = any(item.lower() in {"confirm", "--confirm", "yes"} for item in argv[2:])
        return store.commit(
            scope="global",
            operations=[{"action": "maintenance.gc", "older_than_days": days, "confirm": confirm}],
            idempotency_key=f"operator-gc:{days}:{confirm}",
            reason="operator requested Takyon maintenance GC",
            actor="operator",
        )

    message = " ".join(argv).strip()
    if not message:
        raise SystemExit("empty Takyon command")
    return _run_agent(
        message,
        model=model or os.getenv("TAKYON_MODEL", ""),
        max_turns=int(max_turns or 30),
    )


def takyon_command(args) -> None:
    argv = list(getattr(args, "args", []) or [])
    raw_json = bool(getattr(args, "json", False))
    try:
        result = run_takyon_command(
            argv,
            raw_json=raw_json,
            model=getattr(args, "model", "") or os.getenv("TAKYON_MODEL", ""),
            max_turns=int(getattr(args, "max_turns", 30) or 30),
        )
        _print(result, raw_json=raw_json)
    except SystemExit:
        raise
    except Exception as exc:
        if raw_json:
            _print({"success": False, "error": str(exc)}, raw_json=True)
        else:
            raise SystemExit(f"Takyon error: {exc}") from exc


def takyon_slash_command(raw_args: str, ctx: Any = None) -> str:
    argv = str(raw_args or "").strip().split()
    if not argv:
        return _takyon_help()

    command = argv[0].lower()
    if command in {"help", "-h", "--help"}:
        return _takyon_help()

    if command == "skill":
        if len(argv) < 2:
            return "Usage: /takyon skill <skill-name> <instruction>"
        skill_ref = _resolve_skill_reference(argv[1])
        if not skill_ref:
            return f"Unknown Takyon skill for /takyon: {argv[1]}"
        return _queue_skill_invocation(ctx, skill_ref[0], skill_ref[1], " ".join(argv[2:]).strip())

    if command in _LOCAL_COMMANDS:
        try:
            return _format_slash_value(run_takyon_command(argv))
        except SystemExit as exc:
            return str(exc)
        except Exception as exc:
            return f"Takyon error: {exc}"

    skill_ref = _resolve_skill_reference(command)
    if skill_ref:
        return _queue_skill_invocation(ctx, skill_ref[0], skill_ref[1], " ".join(argv[1:]).strip())

    return _queue_ceo_invocation(ctx, " ".join(argv).strip())


if __name__ == "__main__":
    main()
