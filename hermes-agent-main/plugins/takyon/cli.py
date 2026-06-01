"""Terminal entrypoint for the Takyon plugin."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shlex
import shutil
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from .core import TakyonError, TakyonStore, _normalize_work_focus, _slugify, load_takyon_env, upgrade_businesses


_CEO_PROMPT_PATH = Path(__file__).parent / "prompts" / "ceo.md"
_TAKYON_SKILL_ALIASES = {
    "market-research": "takyon-market-research",
    "build-product": "takyon-build-product",
    "app-runtime": "takyon-app-runtime",
    "distribution": "takyon-distribution",
    "business-pulse": "takyon-business-metrics",
    "business-metrics": "takyon-business-metrics",
    "claude-agent-sdk": "takyon-claude-agent-sdk",
}
_TAKYON_SKILL_PREFIX = "takyon-"


_CLI_ONLY_COMMANDS = {
    "shell",
    "interactive",
    "business",
    "list",
    "campaign",
    "workspace",
    "jobs",
    "harness",
    "command",
    "crons",
    "runtime",
    "runtimes",
    "capabilities",
    "caps",
    "connect",
    "api",
    "show",
    "test",
    "focus",
    "/goal",
    "init",
    "build",
    "upgrade",
}

_REMOVED_COMMANDS = {
    "skills-index": "takyon skills-index was removed. Start a fresh ./takyon run or relaunch the shell to sync bundled skills automatically.",
    "skill-index": "takyon skill-index was removed. Start a fresh ./takyon run or relaunch the shell to sync bundled skills automatically.",
}

_COLOR_ENABLED = (
    sys.stdout.isatty()
    and os.getenv("TAKYON_COLOR") != "0"
    and (os.getenv("TAKYON_COLOR") == "1" or os.getenv("NO_COLOR") != "1")
)
_ANSI = {
    "reset": "\x1b[0m",
    "bold": "\x1b[1m",
    "blink": "\x1b[5m",
    "dim": "\x1b[2m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "cyan": "\x1b[36m",
    "gray": "\x1b[90m",
    "blue": "\x1b[94m",
    "magenta": "\x1b[95m",
    "electric": "\x1b[38;2;0;176;255m",
}
_THEME = {
    "brand": _ANSI["electric"],
    "primary": _ANSI["electric"],
    "secondary": _ANSI["cyan"],
    "skill": _ANSI["cyan"],
    "control": _ANSI["gray"],
    "muted": _ANSI["gray"],
    "success": _ANSI["green"],
    "warning": _ANSI["yellow"],
    "danger": _ANSI["red"],
}

def _color(text: str, code: str) -> str:
    return f"{code}{text}{_ANSI['reset']}" if _COLOR_ENABLED else text


def _blink(text: str) -> str:
    return _color(text, _ANSI["blink"] + _THEME["primary"])


def _bold(text: str) -> str:
    return _color(text, _ANSI["bold"])


def _dim(text: str) -> str:
    return _color(text, _ANSI["dim"])


def _strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _visible_len(text: str) -> int:
    return len(_strip_ansi(text))


def _pad_visible(text: str, width: int) -> str:
    return text + (" " * max(0, width - _visible_len(text)))


def _truncate_plain(text: str, width: int) -> str:
    if width <= 1:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: max(1, width - 3)] + "..."


def _shell_width() -> int:
    columns = shutil.get_terminal_size((96, 24)).columns
    return max(64, min(columns - 2, 112))


def _frame_line(width: int) -> str:
    return _color("+" + ("-" * max(10, width - 2)) + "+", _THEME["muted"])


def _framed_text(text: str, width: int) -> str:
    padding = max(0, width - _visible_len(text) - 4)
    return f"{_color('|', _THEME['muted'])} {text}{' ' * padding} {_color('|', _THEME['muted'])}"


def _scope_label(current_business: str | None) -> str:
    return f"business:{current_business}" if current_business else "global"


def _input_prompt_label(current_business: str | None) -> str:
    scope = _color(_scope_label(current_business), _THEME["secondary"]) if current_business else _dim("global")
    return f"{_color('takyon', _THEME['brand'])}{_dim('/')}{scope}"


def _input_bar_top(current_business: str | None) -> str:
    width = _shell_width()
    label = f" {_input_prompt_label(current_business)} "
    fill = max(0, width - _visible_len(label))
    left = fill // 2
    right = fill - left
    return _color("─" * left, _THEME["muted"]) + label + _color("─" * right, _THEME["muted"])


def _input_prompt(current_business: str | None) -> str:
    if not sys.stdout.isatty():
        return f"takyon/{_scope_label(current_business)} > "
    return "> "


def _render_pixel_mascot_line(line: str, width: int = 16) -> str:
    out: list[str] = []
    for cell in line[:width].ljust(width):
        if cell in {"#", "@"}:
            out.append(_color("██" if _COLOR_ENABLED else "##", _THEME["primary"]))
        elif cell == ".":
            out.append(_color("██" if _COLOR_ENABLED else "##", _THEME["secondary"]))
        elif cell == "=":
            out.append(_color("==", _THEME["secondary"]))
        elif cell == ">":
            out.append(_color("=>", _THEME["secondary"]))
        else:
            out.append("  ")
    return "".join(out)


def _read_mascot_lines() -> list[str]:
    path = _harness_root() / "mascot.txt"
    if path.exists():
        lines = path.read_text(encoding="utf-8", errors="replace").rstrip("\n").splitlines()
        if lines:
            return lines
    return [
        "    ####        ",
        "  ########      ",
        " ##########     ",
        "###..##..###==> ",
        " ############   ",
        "  ########      ",
        "    ####        ",
        "   ##  ##       ",
    ]


def _startup_graphic(current_business: str | None) -> str:
    width = max(92, min(_shell_width(), 112))
    wordmark = [
        " _____     _                      ",
        "|_   _|_ _| | ___   _  ___  _ __  ",
        "  | |/ _` | |/ / | | |/ _ \\| '_ \\ ",
        "  | | (_| |   <| |_| | (_) | | | |",
        "  |_|\\__,_|_|\\_\\\\__, |\\___/|_| |_|",
        "                |___/             ",
    ]
    mascot = _read_mascot_lines()
    rows = [_frame_line(width)]
    for index, line in enumerate(wordmark):
        rows.append(_framed_text(f"{_render_pixel_mascot_line(mascot[index] if index < len(mascot) else '')}  {_color(line, _THEME['brand'])}", width))
    for line in mascot[len(wordmark) :]:
        rows.append(_framed_text(f"{_render_pixel_mascot_line(line)}", width))
    rows.extend([
        _framed_text("", width),
        _framed_text(f"{_bold('Takyon shell')} {_color('ready', _THEME['success'])}  {_dim(str(Path.cwd()))}", width),
        _framed_text(f"{_dim('scope')} {_color(_scope_label(current_business), _THEME['secondary'])}    {_color('plain text', _THEME['primary'])} goes to the scoped CEO", width),
        _framed_text(f"{_color('/', _THEME['primary'])} shows controls and skills    {_color('/use', _THEME['primary'])} switches business scope    {_color('/commands', _THEME['primary'])} lists capabilities", width),
    ])
    rows.append(_frame_line(width))
    return "\n".join(rows)


def register_cli(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help=(
            "Natural language command, a Takyon control command, or a Takyon skill invocation."
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
        epilog=_takyon_help("takyon"),
    )
    register_cli(parser)
    parser.set_defaults(func=takyon_command)
    return parser


def _sync_bundled_skills_startup() -> None:
    try:
        from tools.skills_sync import sync_skills

        sync_skills(quiet=True)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    _sync_bundled_skills_startup()
    takyon_command(args)


def _print(value: Any, *, raw_json: bool = False) -> None:
    if value is None:
        return
    if raw_json:
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return
    if isinstance(value, str):
        print(value)
        return
    print(_format_cli_value(value))


def _format_cli_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return json.dumps(value, indent=2, ensure_ascii=False)

    if "content" in value and "path" in value:
        return str(value.get("content") or "")

    if "files" in value:
        files = value.get("files") or []
        scope = str(value.get("scope") or "")
        business = scope.split("business:", 1)[1].split("/", 1)[0] if "business:" in scope else ""
        root = _business_root(business) if business else None
        if not files:
            where = f" under {root / str(value.get('path') or '.').lstrip('/')}" if root else f" under {value.get('path') or '.'}"
            return f"No files{where}."
        lines = [f"Filesystem: {root}"] if root else []
        lines.extend(
            f"{'/' if item.get('type') == 'dir' else ' '} {item.get('path')}"
            for item in files
        )
        return "\n".join(lines)

    if "business" in value and "ledger" in value and "workspaces" not in value:
        business = value.get("business") or {}
        budget = business.get("budget") or {}
        budget_text = "not set"
        if isinstance(budget, dict) and budget:
            amount = budget.get("amount") or budget.get("cap") or budget.get("limit") or budget.get("monthly_cap")
            currency = budget.get("currency") or "USD"
            budget_text = f"{amount} {currency}" if amount is not None else json.dumps(budget, ensure_ascii=False)
        ledger = value.get("ledger") or []
        lines = [
            f"Budget for business:{business.get('slug') or '<unknown>'}: {budget_text}",
            f"Ledger entries: {len(ledger)}",
        ]
        for item in ledger[:12]:
            lines.append(f"  {item.get('amount')} {item.get('currency')} {item.get('kind')} {item.get('status')} {item.get('id')}")
        return "\n".join(lines)

    if "agent_response" in value:
        lines = [
            f"business:{value.get('business')} mode={value.get('mode') or 'live'}",
        ]
        if value.get("schedule"):
            lines.append(f"CEO wakeup: {value.get('schedule')}")
        lines.extend(["", str(value.get("agent_response") or "").strip()])
        return "\n".join(line for line in lines if line is not None).rstrip()

    if "summary" in value and "deltas_from_previous_pulse" in value and "windows" in value:
        business = value.get("business") or "<unknown>"
        summary = value.get("summary") or {}
        deltas = value.get("deltas_from_previous_pulse") or {}
        current = ((value.get("windows") or {}).get("current_wake_interval") or {}).get("metrics") or {}
        conversion = current.get("conversion") or {}
        revenue = current.get("revenue") or {}
        usage = current.get("usage_cost") or {}
        sales = current.get("sales_signal") or {}
        state = value.get("current_state") or {}
        storage = value.get("storage") or {}
        lines = [
            f"business:{business} pulse",
            f"Generated: {value.get('generated_at')}",
            f"Baseline: {'yes' if value.get('is_first_pulse') else 'no'}",
            f"Business age hours: {state.get('business_age_hours')}",
            f"Wake interval hours: {state.get('wake_interval_hours')}",
            "",
            "Summary:",
        ]
        for key in ("users", "paid_customers", "mrr_cents", "arr_cents", "revenue_cents", "checkout_intents", "usage_events", "actual_cost_microusd", "inbound_messages", "unresolved_inbound"):
            lines.append(f"  {key}: {summary.get(key, 0)}")
        lines.append("")
        lines.append(f"Delta status: {deltas.get('status')}")
        for key, delta in deltas.items():
            if key != "status":
                lines.append(f"  {key}: {delta:+}" if isinstance(delta, int) else f"  {key}: {delta}")
        lines.extend([
            "",
            "Current wake interval:",
            f"  new users: {conversion.get('new_users', 0)}",
            f"  checkouts: {conversion.get('checkout_intents', 0)} ({conversion.get('completed_checkouts', 0)} completed, {conversion.get('test_local_checkouts', 0)} local test)",
            f"  revenue cents: {revenue.get('amount_paid_cents', 0)}",
            f"  usage events: {usage.get('events', 0)}; actual microusd: {usage.get('actual_cost_microusd', 0)}",
            f"  inbound messages: {sales.get('inbound_messages', 0)}; unresolved: {sales.get('unresolved_inbound', 0)}",
            "",
            f"Evidence strength: {(value.get('evidence_strength') or {}).get('score')} / 5",
            f"Readable metrics: {storage.get('human_summary_path') or 'metrics/summary.md'}",
            f"Strategy file: {storage.get('business_model_path') or 'research/strategy.md'}",
        ])
        missing = value.get("missing_metrics") or []
        if missing:
            lines.extend(["", "Missing metrics:", *(f"  {item}" for item in missing[:12])])
        return "\n".join(lines)

    if "business" in value and "mode" in value:
        business = value.get("business") or {}
        if isinstance(business, dict):
            slug = business.get("slug") or business.get("business") or "<unknown>"
            mode = value.get("mode") or business.get("mode") or "live"
        else:
            slug = str(business or "<unknown>")
            mode = value.get("mode") or "live"
        return f"business:{slug} mode -> {mode}"

    if "business" in value and "work_focus" in value:
        business = value.get("business") or {}
        if isinstance(business, dict):
            slug = business.get("slug") or business.get("business") or "<unknown>"
            focus = value.get("work_focus") or business.get("work_focus") or "all"
        else:
            slug = str(business or "<unknown>")
            focus = value.get("work_focus") or "all"
        return f"business:{slug} work focus -> {focus}"

    if "businesses" in value and value.get("scope") == "global":
        businesses = value.get("businesses") or []
        lines = ["Businesses:"]
        if not businesses:
            lines.append("  none yet")
        for item in businesses:
            slug = item.get("slug") or item.get("business") or "<unknown>"
            name = item.get("name") or slug
            goal = item.get("goal") or ""
            status = item.get("status") or "active"
            mode = item.get("mode") or "live"
            focus = item.get("work_focus") or "all"
            focus_text = f"/{focus}" if focus != "all" else ""
            lines.append(f"  {slug} [{status}/{mode}{focus_text}] {name}{f' - {goal}' if goal else ''}")
        controls = value.get("controls") or []
        if controls:
            lines.append("Controls:")
            for item in controls[:12]:
                lines.append(f"  {item.get('scope')} -> {item.get('state')} {item.get('reason') or ''}".rstrip())
        return "\n".join(lines)

    if "business" in value and "workspaces" in value:
        business = value.get("business") or {}
        slug = business.get("slug") or value.get("scope") or "<business>"
        lines = [f"{slug}: {business.get('name') or slug}"]
        if slug and slug != "<business>":
            lines.append(f"Filesystem: {_business_root(str(slug))}")
        if business.get("mode"):
            lines.append(f"Mode: {business.get('mode')}")
        if business.get("work_focus"):
            lines.append(f"Work focus: {business.get('work_focus')}")
        if business.get("goal"):
            lines.append(f"Goal: {business.get('goal')}")
        if business.get("budget"):
            lines.append(f"Budget: {business.get('budget')}")
        conversations = value.get("conversations") or {}
        if conversations:
            lines.append(
                "Conversations: "
                f"{conversations.get('active_threads', 0)} active, "
                f"{conversations.get('unresolved_messages', 0)} unresolved"
            )
            if conversations.get("filesystem_index"):
                lines.append(f"  {conversations.get('filesystem_index')}")
        controls = value.get("controls") or []
        if controls:
            lines.append("Controls:")
            for item in controls[:8]:
                lines.append(f"  {item.get('scope')} -> {item.get('state')} {item.get('reason') or ''}".rstrip())
        workspaces = value.get("workspaces") or []
        if workspaces:
            lines.append("Workspaces:")
            for item in workspaces[:12]:
                lines.append(f"  {item.get('path')} [{item.get('status') or 'active'}]")
        brain = value.get("brain_index") or []
        if brain:
            lines.append("Brain:")
            for item in brain[:12]:
                lines.append(f"  {item.get('path')}")
        jobs = value.get("jobs") or []
        if jobs:
            lines.append("Recent guarded requests:")
            for item in jobs[:8]:
                lines.append(f"  {item.get('kind')} {item.get('status')} {item.get('id')}")
        return "\n".join(lines)

    if "jobs" in value and value.get("success"):
        jobs = value.get("jobs") or []
        if not jobs:
            return "No cron jobs found."
        return "\n".join(
            f"{item.get('id')} {item.get('name') or ''} {item.get('state') or item.get('enabled') or ''} {item.get('schedule_display') or item.get('schedule') or ''}".strip()
            for item in jobs
        )

    if {"tools", "skills"}.intersection(value):
        lines: list[str] = []
        for kind in ("tools", "skills"):
            items = value.get(kind) or []
            if not items:
                continue
            lines.append(f"{kind.title()}:")
            for item in items:
                lines.append(f"  {item.get('name') or item.get('skill')} [{item.get('category')}] {item.get('purpose') or item.get('description') or ''}".rstrip())
        return "\n".join(lines) if lines else json.dumps(value, indent=2, ensure_ascii=False)

    if "results" in value:
        return "\n".join(_format_operation_result(item) for item in value.get("results") or [])

    if "action" in value:
        return _format_operation_result(value)

    return json.dumps(value, indent=2, ensure_ascii=False)


def _format_operation_result(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    action = item.get("action")
    business = str(item.get("business") or "").strip()
    if action == "business.upsert":
        root = _business_root(business) if business else item.get("path")
        return f"business:{business or item.get('business')} filesystem -> {root}"
    if action == "business.mode.set":
        return f"business:{business or item.get('business')} mode -> {item.get('mode')}"
    if action == "business.focus.set":
        return f"business:{business or item.get('business')} work focus -> {item.get('work_focus') or 'all'}"
    if action == "business.delete":
        cron = item.get("cron") or {}
        domains = item.get("domains") or {}
        filesystem = item.get("filesystem") or {}
        if item.get("dry_run"):
            return (
                f"delete preview for business:{business or item.get('business')}; "
                f"filesystem {filesystem.get('files', 0)} files/{filesystem.get('dirs', 0)} dirs; "
                f"cron {len(cron.get('matched') or [])}; "
                f"domains {', '.join(domains.get('candidates') or []) or 'none'}; "
                "rerun with --confirm"
            )
        domain_results = domains.get("results") or []
        domain_text = ", ".join(f"{row.get('domain')}:{row.get('status')}" for row in domain_results) or "none"
        removed_cron = [row for row in cron.get("removed") or [] if row.get("removed")]
        return (
            f"deleted business:{business or item.get('business')}; "
            f"filesystem -> {filesystem.get('path')} removed={filesystem.get('removed')}; "
            f"cron removed={len(removed_cron)}; domains {domain_text}"
        )
    if action == "control.set":
        cron = item.get("cron")
        cron_text = f"; cron {cron}" if cron else ""
        return f"{item.get('scope')} -> {item.get('state')}{cron_text}"
    if action == "ledger.allocate":
        return f"allocated {item.get('amount')} for business:{business or item.get('business')} ledger:{item.get('ledger_entry')}"
    if action == "workspace.upsert":
        workspace = str(item.get("workspace") or "")
        where = f" -> {_business_artifact_path(business, workspace)}" if business and workspace else ""
        return f"workspace {workspace} for business:{business or item.get('business')}{where}"
    if action in {"artifact.write", "memory.write", "artifact.patch"}:
        path = str(item.get("path") or "")
        where = f" -> {_business_artifact_path(business, path)}" if business and path else ""
        return f"{action} {path} for business:{business or item.get('business')}{where}"
    if action == "outreach.local_publish":
        artifact = str(item.get("artifact") or "")
        receipt = str(item.get("receipt") or "")
        bits = [f"test outreach locally published for business:{business or item.get('business')}"]
        if business and artifact:
            bits.append(f"artifact -> {_business_artifact_path(business, artifact)}")
        if business and receipt:
            bits.append(f"receipt -> {_business_artifact_path(business, receipt)}")
        bits.append("external side effects suppressed")
        return "; ".join(bits)
    if action == "app.surface.upsert" and business:
        return f"product surface -> {_business_artifact_path(business, 'product/surface.md')}"
    if action == "app.surface.publish_result" and business:
        status = str(item.get("publish_status") or "not_published")
        url = str(item.get("public_url") or item.get("publish_target") or "")
        suffix = f" ({url})" if url else ""
        return f"app surface publish {status} for business:{business}{suffix}"
    if action == "app.plan.upsert" and business:
        plan = str(item.get("plan_key") or "")
        suffix = f" ({plan})" if plan else ""
        return f"product plans{suffix} -> {_business_artifact_path(business, 'product/plans.md')}"
    if action == "app.budget.set" and business:
        return f"product usage budget -> {_business_artifact_path(business, 'product/usage.md')}"
    if action in {"app.customer.upsert", "app.entitlement.upsert"} and business:
        return f"product customers/entitlements -> {_business_artifact_path(business, 'product/customers.md')}"
    if action == "app.usage.record" and business:
        return f"product usage -> {_business_artifact_path(business, 'product/usage.md')}"
    if action in {"conversation.thread.upsert", "conversation.message.record"} and business:
        path = str(item.get("file") or "")
        where = f" -> {_business_artifact_path(business, path)}" if path else ""
        return f"conversation state for business:{business}{where}"
    if action == "cron.ensure_ceo_wakeup":
        return f"recurring wake schedule for business:{business or item.get('business')}: {item.get('schedule') or item.get('cron_job')}"
    if action == "cron.trigger_ceo_wakeup":
        target = business or item.get("business")
        cron_job = item.get("cron_job") or "unknown"
        if item.get("triggered"):
            ran = item.get("tick_ran")
            ran_text = f"; tick ran {ran} job{'s' if ran != 1 else ''}" if ran is not None else ""
            return f"CEO wake for business:{target} triggered now: {cron_job}{ran_text}"
        error = item.get("error") or "cron job was not found"
        return f"CEO wake for business:{target} could not trigger: {error}"
    if action == "agent.record":
        return f"agent record for business:{business or item.get('business')}: {item.get('agent_run') or item.get('id')}"
    if action == "maintenance.gc":
        return f"GC {'dry run' if item.get('dry_run') else 'completed'} candidates={item.get('candidates')} deleted={item.get('deleted')}"
    return json.dumps(item, indent=2, ensure_ascii=False)


def _business_root(slug: str) -> Path:
    return TakyonStore()._business_root(slug).resolve()


def _business_artifact_path(slug: str, path: str) -> Path:
    return (_business_root(slug) / str(path or "").lstrip("/")).resolve()


def _scope_for_business(slug: str) -> str:
    return f"business:{_slugify(slug)}"


@contextlib.contextmanager
def _business_workspace_execution_context(slug: str, *, operator_user_id: str | None = None):
    from . import storage

    selected_backend = str(os.getenv("TAKYON_STORAGE_BACKEND") or "").strip().lower()
    if not selected_backend:
        yield None
        return
    backend = storage.get_storage_backend()
    with storage.isolated_business_workspace(
        backend,
        slug,
        owner_label=str(operator_user_id or slug),
    ) as workspace_home:
        yield workspace_home


def _parse_business_start_args(
    argv: list[str],
    *,
    usage: str,
    auto_default: bool = False,
) -> tuple[str, str, str, str | None, str | None, bool, bool]:
    tokens = list(argv[1:])
    mode: str | None = None
    schedule: str | None = None
    auto_start = auto_default
    no_auto = False
    clean: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--test":
            mode = "test"
        elif token == "--live":
            mode = "live"
        elif token == "--auto":
            auto_start = True
            no_auto = False
        elif token in {"--no-auto", "--manual"}:
            auto_start = False
            no_auto = True
        elif token == "--schedule":
            index += 1
            if index >= len(tokens):
                raise SystemExit(usage)
            schedule = tokens[index]
        elif token in {"-h", "--help", "help"}:
            raise SystemExit(usage)
        elif token.startswith("--"):
            raise SystemExit(f"unknown create flag {token!r}\n{usage}")
        else:
            clean.append(token)
        index += 1
    if not clean:
        raise SystemExit(usage)
    raw_name = clean[0]
    slug = _slugify(raw_name)
    goal = " ".join(clean[1:]).strip()
    return slug, raw_name, goal, mode, schedule, auto_start, no_auto


def _parse_business_delete_args(argv: list[str]) -> dict[str, Any]:
    usage = "usage: takyon delete <business> [--confirm] [--no-files] [--no-cron] [--no-domains] [--domain <subdomain>]"
    tokens = list(argv[1:])
    if not tokens:
        raise SystemExit(usage)
    result: dict[str, Any] = {
        "business": "",
        "confirm": False,
        "delete_files": True,
        "delete_cron": True,
        "delete_domains": True,
        "subdomains": [],
    }
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"--confirm", "confirm"}:
            result["confirm"] = True
        elif token == "--no-files":
            result["delete_files"] = False
        elif token == "--no-cron":
            result["delete_cron"] = False
        elif token == "--no-domains":
            result["delete_domains"] = False
        elif token == "--domain":
            index += 1
            if index >= len(tokens):
                raise SystemExit(usage)
            result["subdomains"].append(tokens[index])
        elif token.startswith("--"):
            raise SystemExit(f"unknown delete flag {token!r}\n{usage}")
        elif not result["business"]:
            result["business"] = _slugify(token)
        else:
            raise SystemExit(usage)
        index += 1
    if not result["business"]:
        raise SystemExit(usage)
    return result


def _parse_upgrade_args(argv: list[str]) -> dict[str, Any]:
    usage = "usage: takyon upgrade businesses [--dry-run|--apply] [--business <slug> ...]"
    tokens = list(argv[1:])
    if not tokens or tokens[0] != "businesses":
        raise SystemExit(usage)
    dry_run = True
    businesses: list[str] = []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--dry-run":
            dry_run = True
        elif token in {"--apply", "--confirm"}:
            dry_run = False
        elif token == "--business":
            index += 1
            if index >= len(tokens):
                raise SystemExit(usage)
            businesses.append(_slugify(tokens[index]))
        elif token in {"--all"}:
            businesses = []
        elif token in {"-h", "--help", "help"}:
            raise SystemExit(usage)
        elif token.startswith("--"):
            raise SystemExit(f"unknown upgrade flag {token!r}\n{usage}")
        else:
            businesses.append(_slugify(token))
        index += 1
    return {"dry_run": dry_run, "businesses": businesses}


def _idempotency_key(prefix: str, *parts: Any, max_length: int = 180) -> str:
    def key_slug(value: Any, limit: int = 48) -> str:
        raw = str(value or "").strip().lower()
        chars: list[str] = []
        previous_dash = False
        for char in raw:
            if char.isalnum() or char == "_":
                chars.append(char)
                previous_dash = False
            elif char == "-" or char.isspace() or char in ":/.":
                if not previous_dash:
                    chars.append("-")
                    previous_dash = True
            elif not previous_dash:
                chars.append("-")
                previous_dash = True
        slug = "".join(chars).strip("-_")[:limit].strip("-_")
        return slug or "part"

    raw_parts = [str(part) for part in parts if part is not None and str(part) != ""]
    raw = json.dumps([prefix, *raw_parts], ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    human_parts = [key_slug(prefix), *(key_slug(part) for part in raw_parts)]
    human = ":".join(part for part in human_parts if part).strip(":") or "takyon"
    suffix = f":{digest}"
    if len(human) + len(suffix) > max_length:
        human = human[: max(1, max_length - len(suffix))].rstrip(":-")
    return f"{human}{suffix}"


def _resolved_operator_user_id(operator_user_id: str | None = None) -> str:
    return str(operator_user_id or os.getenv("TAKYON_OPERATOR_USER_ID") or "").strip()


def _operator_turn_estimate_cents() -> int:
    raw = str(os.getenv("TAKYON_OPERATOR_TURN_ESTIMATE_CENTS") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    try:
        from .policy import expensive_threshold_cents
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon.policy import expensive_threshold_cents

    return max(0, int(expensive_threshold_cents() or 0))


def _operator_budget_reserve(
    *,
    operator_user_id: str,
    business_slug: str | None,
    reservation_key: str,
    estimate_cents: int | None = None,
) -> tuple[str, int]:
    from .core import _db_backend

    user_id = _resolved_operator_user_id(operator_user_id)
    if not user_id or _db_backend() != "postgres":
        return ("", 0)

    import psycopg

    try:
        from . import billing
        from .runtime_app import resolve_database_url
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon import billing
        from plugins.takyon.runtime_app import resolve_database_url

    amount = _operator_turn_estimate_cents() if estimate_cents is None else max(0, int(estimate_cents))
    if amount <= 0:
        return ("", 0)

    conn = psycopg.connect(resolve_database_url(), autocommit=True)
    try:
        res = billing.reserve(
            conn,
            user_id,
            amount,
            reservation_key,
            business_slug=business_slug or None,
        )
    except billing.InsufficientBalance as exc:
        raise TakyonError(
            "operator budget exhausted: "
            f"need {exc.estimate_cents}c, allowance {exc.allowance_available_cents}c "
            f"+ topup {exc.topup_available_cents}c"
        ) from exc
    finally:
        conn.close()
    return res.key, int(res.allowance_cents + res.topup_cents)


def _operator_budget_finalize(
    *,
    operator_user_id: str,
    business_slug: str | None,
    reservation_key: str,
    reserved_cents: int,
    actual_cents: int,
) -> str:
    from .core import _db_backend

    user_id = _resolved_operator_user_id(operator_user_id)
    if not user_id or not reservation_key or reserved_cents <= 0 or _db_backend() != "postgres":
        return ""

    import psycopg

    try:
        from . import billing
        from .runtime_app import resolve_database_url
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon import billing
        from plugins.takyon.runtime_app import resolve_database_url

    conn = psycopg.connect(resolve_database_url(), autocommit=True)
    warning = ""
    try:
        actual = max(0, int(actual_cents or 0))
        if actual <= 0:
            billing.refund(conn, reservation_key)
            return ""
        if actual <= reserved_cents:
            billing.settle(conn, reservation_key, actual)
            return ""

        overflow = actual - reserved_cents
        overflow_key = f"{reservation_key}:overflow"
        overflow_reserved = 0
        try:
            overflow_res = billing.reserve(
                conn,
                user_id,
                overflow,
                overflow_key,
                business_slug=business_slug or None,
            )
            overflow_reserved = int(overflow_res.allowance_cents + overflow_res.topup_cents)
        except billing.InsufficientBalance:
            warning = (
                f"turn cost exceeded the reserved budget by {overflow}c; "
                "future spend is blocked until the account is topped up."
            )
        billing.settle(conn, reservation_key, reserved_cents)
        if overflow_reserved > 0:
            billing.settle(conn, overflow_key, overflow_reserved)
        return warning
    finally:
        conn.close()


def _business_bootstrap_instruction(slug: str, goal: str, active_mode: str) -> str:
    goal_text = goal or "Use current business state and evidence to define the business goal."
    lines = [
        f"Bootstrap business:{slug} now.",
        "",
        "This is an operational create/build request, not a request for instructions. Do not respond with a command recipe,",
        "a checklist for the operator, or 'want me to start?'. Use Takyon skills and concrete business_* tools now.",
        "",
        f"Business goal: {goal_text}",
        f"Mode: {active_mode or 'live'}",
        "",
        "First read current business state. If relevant assets already exist, advance the missing",
        "highest-impact pieces instead of recreating them.",
        "",
        "Prime directive: find users and become profitable. Re-evaluate ICP, where that ICP concentrates, what",
        "promise/product they would pay for, how Takyon can reach them with current permissions, what evidence changed,",
        "what should change in product/ICP/pricing/distribution, and the highest expected-profit move now.",
        "Treat ICP, offer, product model, pricing, and distribution as revisable beliefs in research/strategy.md.",
        "If conversation, outreach, or user evidence is too large or noisy to inspect cheaply, load",
        "takyon-conversation-followup and use its published follow-up note before deciding.",
        "For channel-native public execution, prefer takyon-x for X and takyon-reddit for Reddit instead of stretching the broad distribution skill.",
        "Call business_calculate_pulse and use takyon-business-metrics to establish the first metrics baseline in metrics/summary.md and research/strategy.md.",
        "Seed or update compact wake notes in metrics/wake-history.md when it helps future scheduled wakes compare what happened, what changed, and what did not move.",
        "Physical subject matter does not imply physical fulfillment; unless the operator explicitly asks this business to sell,",
        "ship, prescribe, perform, or guarantee a physical thing, express the business as a lawful software-native product around the real-world subject.",
        "",
        "In this bootstrap turn, make visible durable progress where safe. For a new or low-evidence business, do",
        "research first: ICP, customer/channel evidence, competitor/pricing notes, strategy, and current hypotheses, and",
        "finish the first real research artifacts before broadening the turn again.",
        "Then normally use takyon-build-product to create or materially advance the smallest useful business-owned",
        "product/site surface that the research supports. Research-first is sequencing, not permission to stop at notes.",
        "For software businesses, the default bootstrap surface mode is app_shell, not landing_page_only:",
        "build a real working product route such as /app, /editor, /tool, or /dashboard unless the operator or current",
        "evidence explicitly calls for a validation/offer-page-first landing.",
        "For software businesses, product work is not meaningfully complete until product/site/ exists with real source and",
        "product/surface.md records that source_path truthfully.",
        "Once product/site/ exists with real source, complete the same-turn build plus business_verify_product_surface",
        "before drifting into later auth, customer, or runtime follow-on work. Let the first honest public surface go live,",
        "then create or continue distribution/campaign/ and run an opening distribution campaign batch through",
        "durable campaign files plus business_publish_outreach intents. The opening distribution campaign is a bootstrap/open-campaign completion contract,",
        "not a forever recurring funnel: normally use at least 3 evidence-backed lanes and 6 total",
        "business_publish_outreach intents, unless a named safety, scope, budget, or operator blocker prevents even",
        "local/mock outreach.",
        "After that opening distribution pass, continue the rest of bootstrap in the same turn.",
        "Treat later auth, account, generate, checkout, billing, and usage wiring as later product work, not as the first public surface.",
        "Do not expand product/runtime.md, product/plans.md, product/customers.md, product/billing.md, or",
        "product/usage.md ahead of that unless the operator explicitly asked for runtime-first work or the current evidence",
        "shows that runtime wiring is the highest-impact move before more source work.",
        "Skip product/source/publication only when current evidence, safety, scope, budget, credentials, or runtime gates make",
        "building the wrong move; record that exact reason as a blocker or research hypothesis.",
        "Use product offer/spec/design/pricing, app plans/surface/budget, website build/publication, chosen distribution files,",
        "guarded jobs or hidden suppressed audit receipts, and the next CEO wake when they are the justified move. Do not stop after",
        "research, source files, or a blocked website publish while the distribution campaign is absent or incomplete. If a",
        "distribution campaign already exists, continue it instead of restarting it. A blocked public URL does not block outreach; use",
        "the business publish_target or a truthful discovery/mock message and name the product blocker.",
        "If something is blocked, record the blocker and continue with local/test artifacts that do not require that provider.",
        "If the chosen artifact has a first-class business tool, use that tool or report the exact missing gate; do not replace videos, local outreach publication, websites, checkout, deploys, or provider-backed work with Markdown summaries.",
        "Never fake auth, sessions, users, entitlements, checkout, subscriptions, outreach sends, deploys, revenue, metrics, or provider results.",
        "If a product feature is not wired to Hermes/Takyon rails, show a visible DEBUG/blocked state instead of demo localStorage, hardcoded users, fake checkout, or fake billing.",
        "",
        "Final response: concise status only. Include business filesystem root, research/strategy created or updated,",
        "files changed, jobs/wakeups, the next CEO action, what is still missing, and whether pricing/research",
        "is evidence-backed or a recorded hypothesis.",
    ]
    if active_mode == "test":
        lines.extend([
            "",
            "Test mode rules: product and website build/publication/deploy are allowed when they are the business-owned",
            "product surface and the normal path, budget, credential, and receipt/job gates pass. Product deploy is Vercel-gated.",
            "Do not send outreach, post to social/forums, buy ads, charge customers, or send marketing emails externally.",
            "For each distribution campaign touch, call business_publish_outreach. If a forum/social channel or provider posting",
            "is unavailable, use local suppressed/mock publication with the intended channel/destination when known. Successful",
            "test-mode touches must create distribution/local-published/ and conversation mirrors; the tool writes",
            "metrics/receipts/outreach/ as hidden audit/debug state, not as deliverables. Otherwise record the exact blocker.",
        ])
    return "\n".join(lines)


def _run_pg_ceo_wake_once(store: TakyonStore, slug: str) -> dict[str, Any]:
    try:
        from . import jobs, worker
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon import jobs, worker

    worker_id = f"cli-wake-{os.getpid()}"
    job_key = _idempotency_key("operator-wake-now", slug, uuid.uuid4().hex)

    with store._connect() as conn:
        with store._leaf_conn(conn) as raw:
            job = jobs.enqueue(
                raw,
                slug,
                "ceo_wake",
                idempotency_key=job_key,
                payload={"estimate_cents": _operator_turn_estimate_cents()},
                max_attempts=1,
            )
            outcome = None
            record = jobs.get_job(raw, job.id)
            for _ in range(20):
                if record is not None and record.status in {"completed", "blocked", "failed"}:
                    break
                outcome = jobs.run_one(
                    raw,
                    worker_id=worker_id,
                    handlers=worker.HANDLERS,
                    kinds=["ceo_wake"],
                )
                record = jobs.get_job(raw, job.id)
                if outcome is None and record is not None and record.status in {"queued", "running"}:
                    continue
            record = jobs.get_job(raw, job.id)

    return {
        "action": "ceo_wake.run",
        "business": slug,
        "job_id": str(job.id),
        "status": str((record.status if record else "") or "queued"),
        "result": (record.result if record else None),
        "error": (record.error if record else None),
        "reserved_cents": int((outcome.reserved_cents if outcome else 0) or 0),
        "actual_cents": int((outcome.actual_cents if outcome else 0) or 0),
    }


def _control(store: TakyonStore, scope: str, state: str, reason: str) -> dict[str, Any]:
    return store.commit(
        scope=scope,
        operations=[{"action": "control.set", "scope": scope, "state": state, "reason": reason}],
        idempotency_key=_idempotency_key("operator-control", scope, state, reason),
        reason=reason,
        actor="operator",
    )


def _takyon_help(prefix: str = "/takyon") -> str:
    try:
        controls = _control_slash_commands()
    except SystemExit:
        controls = []
    control_lines = "\n".join(
        f"  {prefix} {item['name']:<12} {item.get('description') or ''}".rstrip()
        for item in controls
        if item["name"] not in {"exit", "use"}
    )
    if not control_lines:
        control_lines = f"  {prefix} commands"
    return f"""\
{prefix} - Takyon scoped operator and skill namespace

Takyon control commands come from plugins/takyon/harness/settings.json:
{control_lines}

Scoped CEO:
  {prefix} <natural language operator command>
  {prefix} ceo

Takyon skills through Takyon:
  {prefix} <skill-name> <instruction>
  {prefix} skill <skill-name> <instruction>
"""


def _harness_root() -> Path:
    return Path(os.getenv("TAKYON_HARNESS_ROOT") or Path(__file__).parent / "harness").resolve()


def _load_harness_settings() -> dict[str, Any]:
    path = _harness_root() / "settings.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid Takyon harness settings {path}: {exc}") from exc


def _control_slash_commands() -> list[dict[str, Any]]:
    settings = _load_harness_settings()
    configured = settings.get("controlCommands") or []
    if not isinstance(configured, list):
        raise SystemExit("Takyon harness settings controlCommands must be a list")
    commands: list[dict[str, Any]] = []
    for item in configured:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lstrip("/")
        if not name:
            continue
        commands.append({
            "name": name,
            "kind": "control",
            "description": str(item.get("description") or ""),
            "requires_business": bool(item.get("requiresBusiness") or item.get("requires_business")),
            "priority_band": str(item.get("priorityBand") or item.get("priority_band") or "p1_ceo"),
            "usage": str(item.get("usage") or ""),
            "summary": str(item.get("summary") or ""),
            "examples": item.get("examples") if isinstance(item.get("examples"), list) else [],
            "flags": item.get("flags") if isinstance(item.get("flags"), list) else [],
            "palette": str(item.get("palette") or item.get("slashPalette") or "").strip().lower(),
        })
    return commands


def _control_command(name: str) -> dict[str, Any] | None:
    target = str(name or "").strip().lower().lstrip("/")
    for command in _control_slash_commands():
        if str(command.get("name") or "").lower() == target:
            return command
    return None


def _local_command_names() -> set[str]:
    return _CLI_ONLY_COMMANDS | {str(item["name"]) for item in _control_slash_commands()}


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [part.strip().strip("\"'") for part in inner.split(",") if part.strip()]
    return text.strip("\"'")


def _parse_harness_markdown(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    meta: dict[str, Any] = {}
    body = raw
    if raw.startswith("---\n"):
        end = raw.find("\n---", 4)
        if end != -1:
            for line in raw[4:end].strip().splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                meta[key.strip()] = _parse_scalar(value)
            body = raw[end + 4 :].lstrip()
    return {
        "name": str(meta.get("name") or path.stem).strip().lstrip("/"),
        "description": str(meta.get("description") or "").strip(),
        "requires_business": bool(meta.get("requires-business", True)),
        "priority_band": str(meta.get("priority-band") or "").strip(),
        "allowed_tools": [str(item) for item in meta.get("allowed-tools", [])],
        "path": str(path),
        "body": body,
    }


def _list_harness_commands() -> list[dict[str, Any]]:
    command_root = _harness_root() / "commands"
    if not command_root.exists():
        return []
    commands = [
        _parse_harness_markdown(path)
        for path in sorted(command_root.glob("*.md"))
        if path.is_file()
    ]
    return sorted(commands, key=lambda item: str(item["name"]))


def _takyon_skill_entries() -> list[dict[str, Any]]:
    try:
        from agent.skill_commands import get_skill_commands
    except Exception:
        return []

    entries: list[dict[str, Any]] = []
    for command, info in sorted(get_skill_commands().items()):
        bare = str(command or "").lstrip("/")
        if not bare.startswith(_TAKYON_SKILL_PREFIX):
            continue
        skill_dir = Path(str((info or {}).get("skill_dir") or "")).expanduser()
        if skill_dir.parent.name != "takyon":
            continue
        entries.append({
            "command": command,
            "name": bare,
            "description": str((info or {}).get("description") or "").strip(),
            "skill_dir": str(skill_dir).strip(),
        })
    return entries


def _get_harness_command(name: str) -> dict[str, Any] | None:
    target = str(name or "").strip().lower().lstrip("/")
    for command in _list_harness_commands():
        if str(command["name"]).lower() == target:
            return command
    return None


def _render_harness_command(command: dict[str, Any], *, business: str | None, args: list[str], store: TakyonStore) -> str:
    argument_text = " ".join(args).strip()
    workspace_root = ""
    if business:
        workspace_root = str((store.root / "businesses" / business).resolve())
    body = str(command["body"])
    body = body.replace("$ARGUMENTS", argument_text)
    body = body.replace("$BUSINESS", business or "")
    body = body.replace("$WORKSPACE_ROOT", workspace_root)
    header = [
        f"Hermes Takyon harness command: /{command['name']}{f' {argument_text}' if argument_text else ''}",
        f"Business: {business}" if business else "",
        f"Workspace: {workspace_root}" if workspace_root else "",
        f"Description: {command['description']}" if command.get("description") else "",
        "",
    ]
    return "\n".join([line for line in header if line] + [body])


def _format_harness_commands() -> str:
    controls = _control_slash_commands()
    commands = _list_harness_commands()
    skill_entries = _takyon_skill_entries()
    lines = [
        "Takyon shell:",
        "  Plain text always goes to the CEO for the current scope.",
        "  Global is an account/root scope, not the CEO.",
        "",
        "Takyon controls:",
    ]
    if not controls:
        lines.append("  none")
    for command in controls:
        scope = "business" if command.get("requires_business") else "global"
        band = command.get("priority_band") or "unbanded"
        lines.append(f"  /{command['name']:<12} {scope:<8} {band:<12} {command.get('description') or ''}".rstrip())
    lines.append("")
    lines.append("File-backed skill commands:")
    if not commands:
        lines.append("  none")
    for command in commands:
        scope = "business" if command.get("requires_business") else "global"
        band = command.get("priority_band") or "unbanded"
        lines.append(f"  /{command['name']:<12} {scope:<8} {band:<12} {command.get('description') or ''}".rstrip())
    lines.append("")
    lines.append("Takyon skills:")
    if not skill_entries:
        lines.append("  none")
    for item in skill_entries:
        skill_slug = str(item.get("command") or "").lstrip("/")
        lines.append(f"  /{skill_slug:<28} takyon {item.get('description') or ''}".rstrip())
    return "\n".join(lines)


def _slash_entries() -> list[dict[str, Any]]:
    controls = _control_slash_commands()
    harness = [
        {
            "name": command["name"],
            "kind": "skill",
            "description": command.get("description") or "Harness skill",
            "requires_business": bool(command.get("requires_business", True)),
            "priority_band": command.get("priority_band") or "",
        }
        for command in _list_harness_commands()
    ]
    skills = [
        {
            "name": str(item.get("command") or "").lstrip("/"),
            "kind": "skill",
            "description": item.get("description") or "",
            "requires_business": True,
            "priority_band": "p1_ceo",
        }
        for item in _takyon_skill_entries()
    ]
    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    for item in [*controls, *harness, *skills]:
        name = str(item["name"]).strip().lstrip("/")
        if not name or name in seen:
            continue
        seen.add(name)
        entries.append({**item, "name": name})
    return sorted(entries, key=lambda item: str(item["name"]))


def _slash_palette_config() -> dict[str, Any]:
    settings = _load_harness_settings()
    ui = settings.get("ui") if isinstance(settings.get("ui"), dict) else {}
    palette = ui.get("slashPalette") if isinstance(ui.get("slashPalette"), dict) else {}
    default_visible = palette.get("defaultVisible") if isinstance(palette.get("defaultVisible"), list) else []
    context_visible = palette.get("contextVisible") if isinstance(palette.get("contextVisible"), dict) else {}
    return {
        "default_visible": [str(item).strip().lstrip("/") for item in default_visible if str(item).strip()],
        "context_visible": context_visible,
    }


def _business_has_product_surface(current_business: str | None) -> bool:
    if not current_business:
        return False
    try:
        root = _business_root(current_business)
        surface = root / "product" / "surface.md"
        if not surface.exists():
            return False
        text = surface.read_text(encoding="utf-8", errors="replace")
        return "Source path: not set" not in text and "Status: missing" not in text
    except Exception:
        return False


def _slash_prefix(line: str) -> str:
    if not line.startswith("/"):
        return ""
    return line[1:].lstrip().split()[0].lower() if line[1:].strip() else ""


def _should_show_slash_palette(line: str) -> bool:
    return line.startswith("/") and not any(char.isspace() for char in line[1:])


def _visible_slash_entries(entries: list[dict[str, Any]], current_business: str | None) -> list[dict[str, Any]]:
    return [
        item for item in entries
        if not bool(item.get("requires_business")) or bool(current_business)
    ]


def _default_slash_entries(entries: list[dict[str, Any]], current_business: str | None) -> list[dict[str, Any]]:
    visible = _visible_slash_entries(entries, current_business)
    config = _slash_palette_config()
    names = set(config["default_visible"])
    context = config["context_visible"]
    contextual = context.get("businessWithProductSurface") if isinstance(context.get("businessWithProductSurface"), list) else []
    if _business_has_product_surface(current_business):
        names.update(str(item).strip().lstrip("/") for item in contextual if str(item).strip())
    if names:
        filtered = [item for item in visible if str(item["name"]) in names or str(item.get("palette") or "") == "default"]
    else:
        filtered = [item for item in visible if str(item.get("palette") or "") == "default"]
    order = {name: index for index, name in enumerate(config["default_visible"])}
    return sorted(
        filtered,
        key=lambda item: (
            order.get(str(item["name"]), 10_000),
            str(item["name"]),
        ),
    )


def _slash_matches(entries: list[dict[str, Any]], line: str, current_business: str | None) -> list[dict[str, Any]]:
    prefix = _slash_prefix(line)
    visible = _visible_slash_entries(entries, current_business) if prefix else _default_slash_entries(entries, current_business)
    if not prefix:
        return visible
    return [item for item in visible if str(item["name"]).lower().startswith(prefix)]


def _slash_page_size() -> int:
    rows = shutil.get_terminal_size((96, 24)).lines
    return max(6, min(10, rows - 8))


def _render_slash_palette(entries: list[dict[str, Any]], line: str, current_business: str | None, offset: int = 0) -> str:
    prefix = _slash_prefix(line)
    matches = _slash_matches(entries, line, current_business)
    visible_count = len(_visible_slash_entries(entries, current_business))
    width = max(58, min(shutil.get_terminal_size((96, 24)).columns - 6, 96))
    inner = width - 4
    max_rows = _slash_page_size()
    start = max(0, min(offset, max(0, len(matches) - max_rows)))
    end = min(len(matches), start + max_rows)
    title = f"/{prefix}" if prefix else "/"
    default_count = len(_default_slash_entries(entries, current_business))
    count_label = f"{len(matches)}/{visible_count}" if prefix else f"{default_count} shown"
    header = f"{_color('Takyon', _THEME['brand'])} {_dim('slash')} {_color(title, _THEME['primary'])} {_dim(count_label)}"
    context = (
        f"{_dim('scope')} {_color(_scope_label(current_business), _THEME['secondary'])}"
        if current_business
        else f"{_dim('scope')} {_color('global', _THEME['muted'])}  {_dim('attach')} {_color('/use <business>', _THEME['primary'])}"
    )
    overflow_hint = f"  {start + 1}-{end}; type to narrow" if len(matches) > max_rows else ""
    hint = _dim(f"type to search all commands  /commands for full list{overflow_hint}")
    border_top = _color("." + ("-" * (width - 2)) + ".", _THEME["muted"])
    border_bottom = _color("'" + ("-" * (width - 2)) + "'", _THEME["muted"])

    def box(text: str = "") -> str:
        return f"{_color('|', _THEME['muted'])} {_pad_visible(text, inner)} {_color('|', _THEME['muted'])}"

    rows = []
    for entry in matches[start:end]:
        command = _pad_visible(_color(f"/{entry['name']}", _THEME["primary"]), 16)
        desc_width = max(10, inner - 16 - 1)
        rows.append(f"{command} {_dim(_truncate_plain(str(entry.get('description') or ''), desc_width))}")
    if len(matches) > max_rows:
        rows.append(_dim(f"{len(matches) - end} more; type to narrow"))
    if not matches:
        rows.append(f"{_color('no matches', _THEME['warning'])} {_dim('try /businesses or /use <business>')}")
    return "\n".join([
        border_top,
        box(f"{header}  {context}"),
        box(hint),
        box(),
        *(box(row) for row in rows),
        border_bottom,
    ])


def _read_shell_line_prompt_toolkit(current_business: str | None, entries: list[dict[str, Any]]) -> str:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.completion import Completer, Completion

    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):  # noqa: ANN001
            text = document.text_before_cursor
            if not _should_show_slash_palette(text):
                return
            for entry in _slash_matches(entries, text, current_business):
                name = f"/{entry['name']}"
                scope = "business" if entry.get("requires_business") else "global"
                kind = "skill" if entry.get("kind") == "skill" else "control"
                meta = f"{kind} {scope}"
                if entry.get("priority_band"):
                    meta += f" {entry.get('priority_band')}"
                description = str(entry.get("description") or "").strip()
                if description:
                    meta += f"  {description}"
                yield Completion(
                    name,
                    start_position=-len(text),
                    display=name,
                    display_meta=_truncate_plain(meta, 72),
                )

    def slash_toolbar() -> str:
        return ""

    session = PromptSession(
        completer=SlashCompleter(),
        complete_while_typing=True,
        reserve_space_for_menu=max(4, min(_slash_page_size() + 2, 12)),
        bottom_toolbar=slash_toolbar,
    )
    sys.stdout.write(_input_bar_top(current_business) + "\n")
    sys.stdout.flush()
    return session.prompt(_input_prompt(current_business))


def _thinking_ui_config() -> dict[str, Any]:
    settings = _load_harness_settings()
    ui = settings.get("ui") if isinstance(settings.get("ui"), dict) else {}
    thinking = ui.get("thinking") if isinstance(ui.get("thinking"), dict) else {}
    frames = [str(item) for item in thinking.get("frames", []) if str(item)]
    if not frames:
        frames = ["*", "**", "***", " **", "  *", " **"]
    interval_ms = thinking.get("intervalMs", 140)
    try:
        interval = max(0.05, min(float(interval_ms) / 1000.0, 1.0))
    except (TypeError, ValueError):
        interval = 0.14
    return {
        "enabled": _config_bool(thinking.get("enabled"), default=True),
        "label": str(thinking.get("label") or "thinking"),
        "frames": frames,
        "interval": interval,
    }


def _shell_history_config() -> dict[str, Any]:
    settings = _load_harness_settings()
    ui = settings.get("ui") if isinstance(settings.get("ui"), dict) else {}
    history = ui.get("shellHistory") if isinstance(ui.get("shellHistory"), dict) else {}
    try:
        max_turns = int(history.get("maxTurns", 8))
    except (TypeError, ValueError):
        max_turns = 8
    try:
        max_chars = int(history.get("maxCharsPerMessage", 4000))
    except (TypeError, ValueError):
        max_chars = 4000
    return {
        "enabled": _config_bool(history.get("enabled"), default=True),
        "max_turns": max(1, min(max_turns, 20)),
        "max_chars": max(500, min(max_chars, 12000)),
    }


def _shell_progress_config() -> dict[str, Any]:
    settings = _load_harness_settings()
    ui = settings.get("ui") if isinstance(settings.get("ui"), dict) else {}
    progress = ui.get("progress") if isinstance(ui.get("progress"), dict) else {}
    try:
        max_lines = int(progress.get("maxLinesPerTool", 6))
    except (TypeError, ValueError):
        max_lines = 6
    return {
        "enabled": _config_bool(progress.get("enabled"), default=True),
        "show_business_root": _config_bool(progress.get("showBusinessRoot"), default=True),
        "show_durable_writes": _config_bool(progress.get("showDurableWrites"), default=True),
        "max_lines": max(1, min(max_lines, 8)),
    }


def _trim_shell_history_text(text: str, max_chars: int) -> str:
    clean = str(text or "").strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max(0, max_chars - 20)].rstrip() + "\n[truncated]"


def _record_shell_turn(history: list[dict[str, str]], user_text: str, assistant_text: str) -> None:
    config = _shell_history_config()
    if not config["enabled"]:
        return
    history.append({
        "user": _trim_shell_history_text(user_text, config["max_chars"]),
        "assistant": _trim_shell_history_text(assistant_text, config["max_chars"]),
    })
    del history[: max(0, len(history) - int(config["max_turns"]))]


def _format_shell_history(history: list[dict[str, str]] | None) -> str:
    config = _shell_history_config()
    if not config["enabled"] or not history:
        return ""
    lines = ["Recent Takyon shell transcript, newest last:"]
    for index, turn in enumerate(history[-int(config["max_turns"]):], start=1):
        user_text = _trim_shell_history_text(str(turn.get("user") or ""), int(config["max_chars"]))
        assistant_text = _trim_shell_history_text(str(turn.get("assistant") or ""), int(config["max_chars"]))
        if user_text:
            lines.append(f"User {index}: {user_text}")
        if assistant_text:
            lines.append(f"Assistant {index}: {assistant_text}")
    return "\n\n".join(lines).strip()


def _parse_tool_json_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if not isinstance(result, str):
        return {}
    try:
        loaded = json.loads(result)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _tool_progress_lines(name: str, args: dict[str, Any], result: Any) -> list[str]:
    if not str(name or "").startswith("business_"):
        return []
    config = _shell_progress_config()
    data = _parse_tool_json_result(result)
    results = data.get("results") if isinstance(data.get("results"), list) else []
    if not results and data.get("action"):
        results = [data]
    if not results and data.get("success") and str(name or "") == "business_create_app_checkout":
        business = str(data.get("business") or args.get("business") or "").strip()
        lines = []
        if business:
            lines.append(f"checkout intent -> {_business_artifact_path(business, 'product/billing.md')}")
            if str(data.get("external_side_effects") or "") == "suppressed":
                checkout_id = str(data.get("checkout_intent_id") or "")
                if checkout_id:
                    lines.append(f"checkout receipt -> {_business_artifact_path(business, f'metrics/receipts/app-checkout/{checkout_id}.json')}")
        return lines
    if not results and str(name or "") == "business_claude_agent_task":
        business = str(data.get("business") or args.get("business") or "").strip()
        workspace = str(data.get("workspace") or args.get("workspace") or ".").strip() or "."
        lines = []
        if business:
            lines.append(f"agent workspace -> {_business_artifact_path(business, workspace)}")
            verification = data.get("verification") if isinstance(data.get("verification"), dict) else {}
            if verification:
                status = verification.get("status") or "unverified"
                receipt = verification.get("receipt_path") or ""
                suffix = f" -> {_business_artifact_path(business, receipt)}" if receipt else ""
                lines.append(f"product publish check {status}{suffix}")
            agent_record = data.get("agent_record") if isinstance(data.get("agent_record"), dict) else {}
            for line in _tool_progress_lines("business_record_agent", {"business": business}, agent_record)[:1]:
                lines.append(line)
        return lines
    if not results and str(name or "") == "business_verify_product_surface":
        business = str(data.get("business") or args.get("business") or "").strip()
        verification = data.get("verification") if isinstance(data.get("verification"), dict) else {}
        if business and verification:
            status = verification.get("status") or "unverified"
            receipt = verification.get("receipt_path") or ""
            suffix = f" -> {_business_artifact_path(business, receipt)}" if receipt else ""
            return [f"product publish check {status}{suffix}"]
    lines: list[str] = []
    seen_root: set[str] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "")
        business = str(item.get("business") or item.get("business_slug") or args.get("business") or "").strip()
        if config["show_business_root"] and business and business not in seen_root and action == "business.upsert":
            seen_root.add(business)
            lines.append(f"business:{business} filesystem -> {_business_root(business)}")
        if not config["show_durable_writes"]:
            continue
        if action in {"artifact.write", "artifact.patch", "memory.write"}:
            path = str(item.get("path") or "")
            if business and path:
                lines.append(f"file -> {_business_artifact_path(business, path)}")
        elif action == "workspace.upsert":
            workspace = str(item.get("workspace") or "")
            if business and workspace:
                lines.append(f"workspace -> {_business_artifact_path(business, workspace)}")
        elif action == "outreach.local_publish":
            artifact = str(item.get("artifact") or "")
            if business and artifact:
                lines.append(f"local outreach -> {_business_artifact_path(business, artifact)}")
            receipt = str(item.get("receipt") or "")
            if business and receipt:
                lines.append(f"receipt -> {_business_artifact_path(business, receipt)}")
        elif action == "app.surface.upsert":
            if business:
                lines.append(f"product surface -> {_business_artifact_path(business, 'product/surface.md')}")
        elif action == "app.surface.publish_result":
            if business:
                status = str(item.get("publish_status") or "not_published")
                url = str(item.get("public_url") or item.get("publish_target") or "")
                suffix = f" ({url})" if url else ""
                lines.append(f"app surface publish {status} for business:{business}{suffix}")
        elif action == "app.plan.upsert":
            if business:
                plan = str(item.get("plan_key") or "")
                suffix = f" ({plan})" if plan else ""
                lines.append(f"product plans{suffix} -> {_business_artifact_path(business, 'product/plans.md')}")
        elif action == "app.budget.set":
            if business:
                lines.append(f"product usage budget -> {_business_artifact_path(business, 'product/usage.md')}")
        elif action in {"app.customer.upsert", "app.entitlement.upsert"}:
            if business:
                lines.append(f"product customers/entitlements -> {_business_artifact_path(business, 'product/customers.md')}")
        elif action == "app.usage.record":
            if business:
                lines.append(f"product usage -> {_business_artifact_path(business, 'product/usage.md')}")
        elif action in {"conversation.thread.upsert", "conversation.message.record"}:
            path = str(item.get("file") or "")
            if business and path:
                lines.append(f"conversation -> {_business_artifact_path(business, path)}")
        elif action == "business.mode.set":
            if business:
                lines.append(f"business:{business} mode -> {item.get('mode')}")
        elif action == "business.focus.set":
            if business:
                lines.append(f"business:{business} work focus -> {item.get('work_focus') or 'all'}")
        elif action == "cron.ensure_ceo_wakeup":
            if business:
                lines.append(f"wake schedule -> business:{business} {item.get('schedule') or item.get('cron_job')}")
        elif action == "ledger.allocate":
            if business:
                lines.append(f"budget ledger -> business:{business} {item.get('ledger_entry') or ''}".rstrip())
        elif action == "job.enqueue":
            if business:
                lines.append(f"job queued -> business:{business} {item.get('job') or item.get('id') or ''}".rstrip())
        elif action == "agent.record":
            if business:
                lines.append(f"agent record -> business:{business} {item.get('agent_run') or item.get('id') or ''}".rstrip())
    return lines


class _ShellProgress:
    def __init__(self, enabled: bool):
        config = _shell_progress_config()
        self.enabled = bool(enabled and config["enabled"])
        self.max_lines = int(config["max_lines"])
        self.fd: int | None = os.dup(1) if self.enabled else None
        self._last_activity = ""
        self._last_tool_generating = ""

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def emit(self, line: str) -> None:
        if self.fd is None:
            return
        text = f"{_color('->', _THEME['secondary'])} {line}\n"
        try:
            os.write(self.fd, text.encode("utf-8", errors="replace"))
        except OSError:
            self.close()

    def tool_generating(self, name: str) -> None:
        if not name:
            return
        if name == self._last_tool_generating:
            return
        self._last_tool_generating = name
        self.emit(f"preparing tool -> {name}")

    def activity(self, desc: str) -> None:
        text = str(desc or "").strip()
        if not text or text == self._last_activity:
            return
        self._last_activity = text
        self.emit(f"agent -> {text}")

    def tool_progress(self, event_type: str, name: str | None = None, preview: str | None = None, args: dict[str, Any] | None = None, **kwargs: Any) -> None:
        if not name:
            return
        if event_type == "tool.started":
            self._last_tool_generating = ""
            suffix = f" · {preview}" if preview else ""
            self.emit(f"tool started -> {name}{suffix}")
        elif event_type == "tool.completed":
            duration = kwargs.get("duration")
            suffix = f" · {duration:.1f}s" if isinstance(duration, (int, float)) else ""
            self.emit(f"tool completed -> {name}{suffix}")

    def tool_completed(self, _tool_id: str, name: str, args: dict[str, Any], result: Any) -> None:
        if self.fd is None:
            return
        for line in _tool_progress_lines(name, args if isinstance(args, dict) else {}, result)[: self.max_lines]:
            self.emit(line)


@contextlib.contextmanager
def _thinking_indicator(enabled: bool):
    if not enabled or not sys.stdout.isatty():
        yield
        return
    config = _thinking_ui_config()
    if not config["enabled"]:
        yield
        return
    writer_fd = os.dup(1)
    line = f"{_blink('*')}\n"
    try:
        os.write(writer_fd, line.encode("utf-8", errors="replace"))
    except OSError:
        os.close(writer_fd)
        yield
        return
    try:
        yield
    finally:
        try:
            os.write(writer_fd, b"\x1b[1A\x1b[2K")
        except OSError:
            pass
        os.close(writer_fd)


def _read_shell_line(current_business: str | None, entries: list[dict[str, Any]]) -> str:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return input(f"takyon/{current_business or 'global'} > ")
    try:
        return _read_shell_line_prompt_toolkit(current_business, entries)
    except (ImportError, ModuleNotFoundError):
        pass
    sys.stdout.write(_input_bar_top(current_business) + "\n")
    sys.stdout.flush()
    return input(_input_prompt(current_business))


def _business_exists(store: TakyonStore, slug: str) -> bool:
    data = store.read(scope="global", query="list_businesses", limit=200)
    return any(item.get("slug") == slug for item in data.get("businesses", []))


def _require_current_business(current_business: str | None) -> str:
    if not current_business:
        raise SystemExit("Select a business first with /use <business> or create one with /create <business> <goal>.")
    return current_business


def _command_with_current_business(tokens: list[str], current_business: str | None) -> list[str]:
    if not tokens:
        return tokens
    command = tokens[0].lower().lstrip("/")
    if command in {"status", "show"} and len(tokens) == 1 and current_business:
        return ["show", current_business]
    if command == "pulse" and len(tokens) == 1 and current_business:
        return ["pulse", current_business]
    if command in {"files", "workspace"} and current_business:
        return ["files", current_business, *tokens[1:]]
    if command == "read" and current_business:
        return ["read", current_business, *tokens[1:]]
    if command in {"jobs", "campaigns", "capabilities", "caps"} and len(tokens) == 1 and current_business:
        return [command, current_business]
    if command == "test" and current_business:
        mode_args = {"on", "off", "status", "show", "test", "live"}
        if len(tokens) == 1 or tokens[1] in mode_args:
            return ["test", current_business, *tokens[1:]]
    if command == "focus" and current_business:
        focus_args = {"all", "any", "clear", "default", "marketing", "marketing-only", "off", "product", "product-only", "show", "status"}
        if len(tokens) == 1 or tokens[1] in focus_args:
            return ["focus", current_business, *tokens[1:]]
    if command in {"wake", "run", "goal", "/goal"} and current_business:
        return [command, current_business, *tokens[1:]]
    if command == "delete" and current_business:
        first_arg_is_flag = len(tokens) >= 2 and (tokens[1].startswith("--") or tokens[1] == "confirm")
        if len(tokens) == 1 or first_arg_is_flag:
            return ["delete", current_business, *tokens[1:]]
    if command in {"budget"} and current_business and (len(tokens) == 1 or tokens[1] in {"show", "status", "set"}):
        return [command, *tokens[1:2], current_business, *tokens[2:]] if len(tokens) > 1 else [command, current_business]
    if command in {"memory"} and current_business and (len(tokens) == 1 or tokens[1] in {"list", "record"}):
        return [command, *tokens[1:2], current_business, *tokens[2:]] if len(tokens) > 1 else [command, "list", current_business]
    if command in {"pause", "resume", "kill"} and len(tokens) == 1 and current_business:
        return [command, "business", current_business]
    return tokens


def _looks_like_slug(value: str) -> bool:
    try:
        _slugify(value)
        return True
    except Exception:
        return False


def _operator_context_message(message: str, current_business: str | None) -> str:
    if current_business:
        return (
            f"Scope: business:{current_business}\n"
            "CEO role: scoped business operator.\n\n"
            f"Operator request:\n{message}\n\n"
            "First read this business state with Takyon business tools. Honor the business work_focus field "
            "if it is marketing-only or product-only. Keep all durable writes business-scoped."
        )
    return (
        "Scope: global\n"
        "CEO role: account/root-scope operator. Global is not the CEO; it is the top-level Takyon scope.\n\n"
        f"Operator request:\n{message}\n\n"
        "Use global reads for businesses, credentials, policy, skills, and budgets. "
        "For any business/product/customer state change, create or select the business and use concrete business_* tools."
    )


def _format_ceo_focus(current_business: str | None, store: TakyonStore, model: str = "") -> str:
    config = _read_model_config(store)
    resolved_model = model or os.getenv("TAKYON_MODEL", "") or config.get("model") or "(not set)"
    provider = config.get("provider") or "(not set)"
    lines = [
        f"Scope: {_scope_label(current_business)}",
        "Scoped CEO: already active for plain text.",
    ]
    if current_business:
        lines.append("Plain text will operate inside this business and keep durable writes business-scoped.")
        try:
            business = (store.read(scope=_scope_for_business(current_business), query="summary").get("business") or {})
            lines.append(f"Work focus: {business.get('work_focus') or 'all'}")
        except Exception:
            pass
    else:
        lines.append("Plain text will operate at the global Takyon account scope; use /use <business> to enter a business.")
    lines.extend([
        f"Model: {resolved_model}",
        f"Provider: {provider}",
    ])
    return "\n".join(lines)


def _config_path(store: TakyonStore) -> Path:
    return store.root / "config.yaml"


def _secrets_path(store: TakyonStore) -> Path:
    return store.root.parent / "secrets" / ".env"


def _read_model_config(store: TakyonStore) -> dict[str, str]:
    path = _config_path(store)
    provider = ""
    model = ""
    claude_agent_model = ""
    response_style = ""
    show_agent_activity = ""
    shell_enhanced_input = ""
    auto_schedule_ceo_on_create = ""
    default_ceo_schedule = ""
    if path.exists():
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            model_data = data.get("model") or {}
            provider = str(model_data.get("provider") or "")
            model = str(model_data.get("default") or model_data.get("model") or "")
            claude_agent_model = str(
                model_data.get("claude_agent_default")
                or model_data.get("deep_work_default")
                or ""
            )
            conversation_data = data.get("conversation") or {}
            if isinstance(conversation_data, dict):
                response_style = str(conversation_data.get("response_style") or "")
                show_agent_activity = str(conversation_data.get("show_agent_activity") or "")
            shell_data = data.get("shell") or {}
            if isinstance(shell_data, dict):
                shell_enhanced_input = str(shell_data.get("enhanced_input") or "")
            business_data = data.get("business") or {}
            if isinstance(business_data, dict):
                auto_schedule_ceo_on_create = str(business_data.get("auto_schedule_ceo_on_create") or "")
                default_ceo_schedule = str(business_data.get("default_ceo_schedule") or "")
        except Exception:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("provider:"):
                    provider = stripped.split(":", 1)[1].strip()
                if stripped.startswith("default:"):
                    model = stripped.split(":", 1)[1].strip()
                if stripped.startswith("claude_agent_default:"):
                    claude_agent_model = stripped.split(":", 1)[1].strip()
                if stripped.startswith("response_style:"):
                    response_style = stripped.split(":", 1)[1].strip()
                if stripped.startswith("show_agent_activity:"):
                    show_agent_activity = stripped.split(":", 1)[1].strip()
                if stripped.startswith("enhanced_input:"):
                    shell_enhanced_input = stripped.split(":", 1)[1].strip()
                if stripped.startswith("auto_schedule_ceo_on_create:"):
                    auto_schedule_ceo_on_create = stripped.split(":", 1)[1].strip()
                if stripped.startswith("default_ceo_schedule:"):
                    default_ceo_schedule = stripped.split(":", 1)[1].strip()
    return {
        "provider": provider,
        "model": model,
        "claude_agent_model": claude_agent_model,
        "response_style": response_style,
        "show_agent_activity": show_agent_activity,
        "shell_enhanced_input": shell_enhanced_input,
        "auto_schedule_ceo_on_create": auto_schedule_ceo_on_create,
        "default_ceo_schedule": default_ceo_schedule,
        "path": str(path),
    }


def _config_bool(value: Any, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _require_agent_model_config(config: dict[str, str], *, model_override: str | None = None) -> str:
    provider = config.get("provider", "")
    resolved_model = model_override or os.getenv("TAKYON_MODEL", "") or config.get("model", "")
    if provider and resolved_model:
        return resolved_model
    missing = []
    if not provider:
        missing.append("model.provider")
    if not resolved_model:
        missing.append("model.default")
    path = config.get("path") or str(_config_path(TakyonStore()))
    raise TakyonError(
        f"Takyon model config missing {', '.join(missing)} in {path}. "
        "Run `takyon model set <provider> <model>` or copy the workspace config into this TAKYON_HOME."
    )


def _write_model_config(store: TakyonStore, provider: str, model: str) -> dict[str, str]:
    path = _config_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            model_data = data.get("model") if isinstance(data.get("model"), dict) else {}
            model_data = dict(model_data)
            model_data["provider"] = provider
            model_data["default"] = model
            data["model"] = model_data
            data.setdefault("security", {"redact_secrets": True})
            path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
            path.chmod(0o600)
            return {"provider": provider, "model": model, "path": str(path)}
        except Exception:
            pass
    path.write_text(
        "model:\n"
        f"  provider: {provider}\n"
        f"  default: {model}\n\n"
        "security:\n"
        "  redact_secrets: true\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return {"provider": provider, "model": model, "path": str(path)}


def _format_model_config(store: TakyonStore) -> str:
    data = _read_model_config(store)
    model = data.get("model") or "(not set)"
    claude_agent_model = data.get("claude_agent_model") or "(inherits default)"
    response_style = data.get("response_style") or "(not set)"
    show_agent_activity = data.get("show_agent_activity") or "(not set)"
    provider = data.get("provider") or "(not set)"
    return (
        f"Model provider: {provider}\n"
        f"Conversational model: {model}\n"
        f"Claude Agent SDK model: {claude_agent_model}\n"
        f"Response style: {response_style}\n"
        f"Agent activity: {show_agent_activity}\n"
        f"Config: {data.get('path')}"
    )


def _setup_paths(store: TakyonStore) -> str:
    store.root.mkdir(parents=True, exist_ok=True)
    secrets = _secrets_path(store)
    secrets.parent.mkdir(parents=True, exist_ok=True)
    if not secrets.exists():
        secrets.write_text("", encoding="utf-8")
        secrets.chmod(0o600)
    link = store.root / ".env"
    if not link.exists():
        try:
            link.symlink_to(Path("..") / "secrets" / ".env")
        except OSError:
            pass
    return "\n".join([
        f"Takyon home: {store.root}",
        f"Config: {_config_path(store)}",
        f"Secrets: {secrets}",
        "Secrets are never printed by this command.",
    ])


def _secret_command(store: TakyonStore, argv: list[str]) -> str:
    path = _secrets_path(store)
    if len(argv) < 2 or argv[1] in {"list", "ls"}:
        load_takyon_env()
        names: list[str] = []
        for candidate in [path, store.root / ".env"]:
            if not candidate.exists():
                continue
            for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                names.append(stripped.split("=", 1)[0].removeprefix("export ").strip())
        names = sorted(set(name for name in names if name))
        return "Secret keys:\n" + ("\n".join(f"  {name}=<redacted>" for name in names) if names else "  none found")
    if argv[1] != "set" or len(argv) < 4:
        raise SystemExit("usage: takyon secret list | takyon secret set KEY VALUE")
    key = argv[2].strip()
    if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
        raise SystemExit("secret key must be an env-style name")
    value = " ".join(argv[3:])
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    prefix = f"{key}="
    updated = False
    next_lines = []
    for line in lines:
        if line.strip().startswith(prefix) or line.strip().startswith(f"export {prefix}"):
            next_lines.append(f"{key}={value}")
            updated = True
        else:
            next_lines.append(line)
    if not updated:
        next_lines.append(f"{key}={value}")
    path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o600)
    return f"Stored {key}=<redacted> in {path}"


def _seed_platform_owner_at_startup(store: TakyonStore) -> None:
    """Serving-flip startup seed for the interactive shell (Phase 8, mediationplan.md owner-wiring
    finding). On the Postgres backend the shell/CEO owns every business it creates as the single
    platform owner, but ``business.upsert`` resolves that owner READ-ONLY and blocks if it is
    unprovisioned (invariant #8) — so seed it here, once, at shell start. Idempotent, and a guarded
    no-op on SQLite (no ``users`` table to seed). Never blocks the shell from starting: a Postgres
    hiccup is surfaced to stderr (a later ``/create`` would block with its own actionable reason)
    rather than crashing the operator's session. The one-time raw API key is minted only on the very
    first Postgres startup and shown exactly once."""
    try:
        _user_id, raw_key = store.seed_platform_owner()
    except Exception as exc:  # noqa: BLE001 - never block the shell on a startup seed failure
        sys.stderr.write(f"[takyon] platform-owner seed skipped: {exc}\n")
        return
    if raw_key:
        sys.stderr.write(
            "\n[takyon] Provisioned the platform owner on Postgres.\n"
            "[takyon] One-time API key (shown ONCE — store it securely):\n"
            f"[takyon]   {raw_key}\n\n"
        )


def _interactive_shell(*, initial_business: str | None, model: str, max_turns: int) -> None:
    store = TakyonStore()
    _seed_platform_owner_at_startup(store)
    current_business = _slugify(initial_business) if initial_business else None
    if current_business and not _business_exists(store, current_business):
        print(f"[takyon] business:{current_business} is not initialized yet. /create {current_business} <goal> will create it.")

    entries = _slash_entries()
    print(_startup_graphic(current_business))
    shell_history: list[dict[str, str]] = []

    while True:
        try:
            line = _read_shell_line(current_business, entries)
        except EOFError:
            print()
            return
        except KeyboardInterrupt:
            print()
            continue
        line = line.strip()
        if not line:
            continue
        if line in {"/exit", "exit", "/quit", "quit"} or line.lstrip("/") in {"exit", "quit"}:
            return
        try:
            output, current_business = _handle_shell_line(
                line,
                current_business=current_business,
                store=store,
                model=model,
                max_turns=max_turns,
                shell_history=shell_history,
            )
            if output:
                print(output)
            _record_shell_turn(shell_history, line, output)
            entries = _slash_entries()
        except SystemExit as exc:
            if str(exc):
                output = f"Takyon: {exc}"
                print(output)
                _record_shell_turn(shell_history, line, output)
        except KeyboardInterrupt:
            print("Takyon: interrupted")
        except Exception as exc:
            output = f"Takyon error: {exc}"
            print(output)
            _record_shell_turn(shell_history, line, output)


def _handle_shell_line(
    line: str,
    *,
    current_business: str | None,
    store: TakyonStore,
    model: str,
    max_turns: int,
    shell_history: list[dict[str, str]] | None = None,
    operator_user_id: str | None = None,
) -> tuple[str, str | None]:
    is_slash = line.startswith("/")
    raw = line.lstrip("/") if is_slash else line
    if is_slash and not raw.strip():
        return _render_slash_palette(_slash_entries(), "/", current_business), current_business
    tokens = shlex.split(raw)
    if not tokens:
        return "", current_business
    command = tokens[0].lower()
    local_answer = _local_shell_help_answer(raw, current_business=current_business)
    if local_answer:
        return local_answer, current_business

    if is_slash and command == "ceo":
        return _format_ceo_focus(current_business, store, model), current_business

    if command == "use":
        if len(tokens) < 2:
            raise SystemExit("usage: /use <business>")
        slug = _slugify(tokens[1])
        if not _business_exists(store, slug):
            raise SystemExit(f"business:{slug} does not exist yet. Use /create {slug} <goal>.")
        return f"Using business:{slug}", slug

    if command in {"help", "commands", "skills", "harness"}:
        return _format_harness_commands(), current_business

    if command in {"create", "build", "init"}:
        if len(tokens) < 2:
            raise SystemExit('usage: /create [--test|--live] [--no-auto] [--schedule "every 6h"] <business> [goal]')
        command_argv = ["create", *tokens[1:]]
        slug, _raw_name, _goal, _mode, _schedule, _auto_start, _no_auto = _parse_business_start_args(
            command_argv,
            usage='usage: /create [--test|--live] [--no-auto] [--schedule "every 6h"] <business> [goal]',
            auto_default=True,
        )
        result = run_takyon_command(
            command_argv,
            model=model,
            max_turns=max_turns,
            show_activity=False,
            show_indicator=True,
            shell_history=shell_history,
            operator_user_id=operator_user_id,
        )
        return _format_cli_value(result), slug

    harness_command = _get_harness_command(command)
    if harness_command:
        business = current_business
        if harness_command.get("requires_business"):
            business = _require_current_business(current_business)
        message = _render_harness_command(harness_command, business=business, args=tokens[1:], store=store)
        return _run_agent(
            message,
            model=model or os.getenv("TAKYON_MODEL", ""),
            max_turns=max_turns,
            show_activity=False,
            show_indicator=True,
            shell_history=shell_history,
            operator_user_id=operator_user_id,
            current_business=business,
        ), current_business

    if command in _local_command_names() and command != "ceo":
        normalized = _command_with_current_business(tokens, current_business)
        result = run_takyon_command(
            normalized,
            model=model,
            max_turns=max_turns,
            show_activity=False,
            show_indicator=True,
            shell_history=shell_history,
            operator_user_id=operator_user_id,
        )
        next_business = current_business
        if normalized and normalized[0].lower() == "delete":
            deleted = any(
                isinstance(item, dict)
                and item.get("action") == "business.delete"
                and not item.get("dry_run")
                and str(item.get("business") or "") == str(current_business or "")
                for item in (result.get("results") if isinstance(result, dict) else []) or []
            )
            if deleted:
                next_business = None
        return _format_cli_value(result), next_business

    if is_slash:
        skill_ref = _resolve_skill_reference(command)
        if skill_ref:
            if command != "ceo":
                _require_current_business(current_business)
            instruction = " ".join(tokens[1:]).strip() or f"Use the {command} skill for the current scope."
            if skill_ref[0] == "slash":
                from agent.skill_commands import build_skill_invocation_message

                message = build_skill_invocation_message(
                    skill_ref[1],
                    instruction,
                    runtime_note="Invoked through the Takyon scoped shell.",
                )
            else:
                message = _plugin_skill_invocation_message(skill_ref[1], instruction) or instruction
            return _run_agent(
                _operator_context_message(message, current_business),
                model=model or os.getenv("TAKYON_MODEL", ""),
                max_turns=max_turns,
                show_activity=False,
                show_indicator=True,
                shell_history=shell_history,
                operator_user_id=operator_user_id,
                current_business=current_business,
            ), current_business
        return f"Unknown slash command: /{command}. Use /commands.", current_business

    message = _operator_context_message(line, current_business)
    return _run_agent(
        message,
        model=model or os.getenv("TAKYON_MODEL", ""),
        max_turns=max_turns,
        show_activity=False,
        show_indicator=True,
        shell_history=shell_history,
        operator_user_id=operator_user_id,
        current_business=current_business,
    ), current_business


def _local_shell_help_answer(raw: str, *, current_business: str | None) -> str:
    text = " ".join(str(raw or "").strip().lower().replace("?", " ").split())
    if not text:
        return ""
    help_tokens = {"how", "what", "help", "usage", "show", "tell", "explain"}
    wants_help = bool(help_tokens & set(text.split()[:4]))
    if wants_help:
        matched = _match_self_help_command(text)
        if matched:
            return _format_control_command_help(matched)
    return ""


def _match_self_help_command(text: str) -> str:
    settings = _load_harness_settings()
    configured = settings.get("selfHelp") or []
    if not isinstance(configured, list):
        return ""
    words = set(text.split())
    for item in configured:
        if not isinstance(item, dict):
            continue
        required = [str(token).strip().lower() for token in item.get("whenAll") or [] if str(token).strip()]
        command = str(item.get("command") or "").strip().lstrip("/")
        if command and required and all(token in words for token in required):
            return command
    return ""


def _format_control_command_help(name: str) -> str:
    command = _control_command(name)
    if not command:
        return ""
    lines: list[str] = []
    summary = str(command.get("summary") or "").strip()
    description = str(command.get("description") or "").strip()
    usage = str(command.get("usage") or "").strip()
    if summary:
        lines.append(summary)
    elif description:
        lines.append(description)
    if usage:
        lines.extend(["", "Usage:", f"  {usage}"])
    examples = command.get("examples") or []
    for example in examples:
        if not isinstance(example, dict):
            continue
        label = str(example.get("label") or "Example").strip()
        value = str(example.get("command") or "").strip()
        if value:
            lines.extend(["", f"{label}:", f"  {value}"])
    flags = command.get("flags") or []
    if flags:
        width = max(len(str(flag.get("name") or "")) for flag in flags if isinstance(flag, dict)) if any(isinstance(flag, dict) for flag in flags) else 0
        lines.extend(["", "Flags:"])
        for flag in flags:
            if not isinstance(flag, dict):
                continue
            flag_name = str(flag.get("name") or "").strip()
            flag_description = str(flag.get("description") or "").strip()
            if flag_name:
                lines.append(f"  {flag_name:<{width}}  {flag_description}".rstrip())
    return "\n".join(lines).strip()


def _format_slash_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return _format_cli_value(value)


def _resolve_skill_reference(name: str) -> tuple[str, str] | None:
    clean = str(name or "").strip().lstrip("/")
    if not clean:
        return None
    try:
        from agent.skill_commands import resolve_skill_command_key

        resolved = resolve_skill_command_key(clean)
        if resolved:
            return ("slash", resolved)
        alias = _TAKYON_SKILL_ALIASES.get(clean)
        if alias:
            resolved = resolve_skill_command_key(alias)
            if resolved:
                return ("slash", resolved)
    except Exception:
        pass
    return None


def _queue_skill_invocation(ctx: Any, skill_ref: str, instruction: str) -> str:
    try:
        from agent.skill_commands import build_skill_invocation_message

        msg = build_skill_invocation_message(
            skill_ref,
            instruction,
            runtime_note="Invoked through the /takyon skill namespace.",
        )
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
        f"{_operator_context_message(message, None)}\n\n"
        "Use the Takyon CEO prompt, real Takyon skills from the Hermes skills index, and concrete business_* tools. Keep business state isolated."
    )
    if ctx is not None and hasattr(ctx, "inject_message") and ctx.inject_message(prompt):
        return "Queued Takyon CEO command."
    return _run_agent(
        message,
        model=os.getenv("TAKYON_MODEL", ""),
        max_turns=int(os.getenv("TAKYON_MAX_TURNS", "30") or 30),
    )


@contextlib.contextmanager
def _silence_process_stdio():
    stdout_fd = stderr_fd = devnull_fd = None
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        stdout_fd = os.dup(1)
        stderr_fd = os.dup(2)
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        if stdout_fd is not None:
            os.dup2(stdout_fd, 1)
            os.close(stdout_fd)
        if stderr_fd is not None:
            os.dup2(stderr_fd, 2)
            os.close(stderr_fd)
        if devnull_fd is not None:
            os.close(devnull_fd)


def _run_agent_with_meta(
    message: str,
    *,
    model: str,
    max_turns: int,
    show_activity: bool | None = None,
    show_indicator: bool = False,
    shell_history: list[dict[str, str]] | None = None,
    operator_user_id: str | None = None,
    current_business: str | None = None,
) -> tuple[str, dict[str, Any]]:
    load_takyon_env()
    from run_agent import AIAgent

    ceo_prompt = _load_ceo_prompt()
    model_config = _read_model_config(TakyonStore())
    resolved_model = _require_agent_model_config(model_config, model_override=model)
    provider = model_config.get("provider", "")
    response_style = model_config.get("response_style", "").strip().lower()
    configured_activity = _config_bool(model_config.get("show_agent_activity"), default=False)
    show_agent_activity = configured_activity if show_activity is None else bool(show_activity)
    history_text = _format_shell_history(shell_history)
    history_block = f"{history_text}\n\nCurrent turn:\n" if history_text else ""
    prompt = (
        "Takyon operator command:\n\n"
        f"{history_block}{message}\n\n"
        f"Configured response style: {response_style or 'default'}.\n"
        "Use source-of-truth state, command behavior, declared skill metadata, and loaded skills before assumptions. "
        "Resolve short follow-ups like 'make #1' against the recent shell transcript when it is provided. "
        "If you do not know a fact from available context or tools, say so briefly. "
        "Default to action for operational business requests. If the operator gives a business slug, goal, or clear choice "
        "and asks to create, make, set up, build, start, run, continue, or operate it, use the business tools now. "
        "Do not answer with a command recipe, implementation checklist, or 'say X and I will' handoff unless the operator "
        "explicitly asks for explanation only or says not to implement. "
        "Use concrete business_* tools for all durable business state changes. "
        "If an operator asks for a video/image, outreach publication, website, deploy, checkout, provider call, or other first-class artifact, use the matching business tool or report the exact missing gate; do not substitute a Markdown brief. "
        "Do not narrate private setup with phrases like 'Good, I have the full business context' or 'Now I will'; answer, act, ask one necessary question, or report the blocker. "
        "Read business state before broad changes. Honor a business work_focus of marketing or product as an operator constraint "
        "for manual turns and scheduled wakes. Keep every write business-scoped. "
        "Do not claim a file write, budget allocation, job enqueue, agent record, wakeup schedule, auth state, billing state, "
        "checkout, subscription, entitlement, deploy, outreach, revenue, metric, or provider result succeeded unless the specific "
        "business tool returned success or a concrete receipt exists. Never fake product behavior; use Hermes rails or a visible DEBUG/blocked state."
    )

    progress = _ShellProgress(show_indicator and not show_agent_activity)
    resolved_operator_user_id = _resolved_operator_user_id(operator_user_id)
    reservation_key = ""
    reserved_cents = 0
    billing_warning = ""
    agent_box: dict[str, Any] = {}
    session_context_tokens: list[Any] = []

    def invoke() -> tuple[dict[str, Any], int]:
        agent = AIAgent(
            provider=provider or None,
            model=resolved_model,
            max_iterations=max_turns,
            enabled_toolsets=["takyon", "web", "skills", "todo"],
            disabled_toolsets=["cronjob", "messaging", "memory", "session_search", "terminal", "file", "browser", "code_execution"],
            ephemeral_system_prompt=ceo_prompt,
            load_soul_identity=False,
            skip_memory=True,
            skip_context_files=True,
            platform="takyon",
            quiet_mode=not show_agent_activity,
            tool_progress_callback=progress.tool_progress if progress.enabled else None,
            tool_gen_callback=progress.tool_generating if progress.enabled else None,
            tool_complete_callback=progress.tool_completed if progress.enabled else None,
        )
        agent_box["agent"] = agent
        agent._memory_nudge_interval = 0
        agent._skill_nudge_interval = 0
        agent.activity_callback = progress.activity if progress.enabled else None
        agent.suppress_status_output = not show_agent_activity
        result = agent.run_conversation(
            prompt,
            stream_callback=None if show_agent_activity else (lambda _delta: None),
        )
        actual_cents = max(
            0,
            int(round(float(getattr(agent, "session_estimated_cost_usd", 0.0) or 0.0) * 100)),
        )
        return result, actual_cents

    try:
        if resolved_operator_user_id:
            reservation_key, reserved_cents = _operator_budget_reserve(
                operator_user_id=resolved_operator_user_id,
                business_slug=current_business,
                reservation_key=_idempotency_key(
                    "operator-turn",
                    current_business or "global",
                    uuid.uuid4().hex,
                ),
            )
        workspace_context = (
            _business_workspace_execution_context(
                current_business,
                operator_user_id=resolved_operator_user_id,
            )
            if current_business
            else contextlib.nullcontext(None)
        )
        with workspace_context as workspace_home:
            if resolved_operator_user_id or workspace_home is not None:
                try:
                    from gateway.session_context import set_session_vars

                    session_context_tokens = set_session_vars(
                        session_key="",
                        user_id=resolved_operator_user_id,
                        workspace_root=str(workspace_home or ""),
                    )
                except Exception:
                    session_context_tokens = []
            if show_agent_activity:
                result, actual_cents = invoke()
            else:
                with _thinking_indicator(show_indicator and not progress.enabled):
                    with _silence_process_stdio():
                        result, actual_cents = invoke()
        if reservation_key:
            billing_warning = _operator_budget_finalize(
                operator_user_id=resolved_operator_user_id,
                business_slug=current_business,
                reservation_key=reservation_key,
                reserved_cents=reserved_cents,
                actual_cents=actual_cents,
            )
        final_response = str(result.get("final_response") or "")
        if billing_warning:
            final_response = (
                final_response.rstrip()
                + ("\n\n" if final_response.strip() else "")
                + f"[Budget warning] {billing_warning}"
            )
        return final_response, {
            "actual_cost_cents": actual_cents,
            "reserved_cents": reserved_cents,
            "billing_warning": billing_warning,
        }
    except Exception:
        actual_cents = max(
            0,
            int(
                round(
                    float(
                        getattr(agent_box.get("agent"), "session_estimated_cost_usd", 0.0)
                        or 0.0
                    )
                    * 100
                )
            ),
        )
        if reservation_key:
            billing_warning = _operator_budget_finalize(
                operator_user_id=resolved_operator_user_id,
                business_slug=current_business,
                reservation_key=reservation_key,
                reserved_cents=reserved_cents,
                actual_cents=actual_cents,
            )
        raise
    finally:
        if session_context_tokens:
            try:
                from gateway.session_context import clear_session_vars

                clear_session_vars(session_context_tokens)
            except Exception:
                pass
        progress.close()


def _run_agent(
    message: str,
    *,
    model: str,
    max_turns: int,
    show_activity: bool | None = None,
    show_indicator: bool = False,
    shell_history: list[dict[str, str]] | None = None,
    operator_user_id: str | None = None,
    current_business: str | None = None,
) -> str:
    response, _meta = _run_agent_with_meta(
        message,
        model=model,
        max_turns=max_turns,
        show_activity=show_activity,
        show_indicator=show_indicator,
        shell_history=shell_history,
        operator_user_id=operator_user_id,
        current_business=current_business,
    )
    return response


def _load_ceo_prompt() -> str:
    return _CEO_PROMPT_PATH.read_text(encoding="utf-8")


def run_takyon_command(
    argv: list[str],
    *,
    raw_json: bool = False,
    model: str = "",
    max_turns: int = 30,
    show_activity: bool | None = None,
    show_indicator: bool = False,
    shell_history: list[dict[str, str]] | None = None,
    operator_user_id: str | None = None,
) -> Any:
    resolved_operator_user_id = _resolved_operator_user_id(operator_user_id)
    store = TakyonStore(operator_user_id=resolved_operator_user_id)

    if not argv:
        return store.read(scope="global", query="list_businesses")

    command = argv[0].lower()
    removed_message = _REMOVED_COMMANDS.get(command)
    if removed_message:
        raise SystemExit(removed_message)

    if command in {"help", "-h", "--help"}:
        if len(argv) >= 2:
            command_help = _format_control_command_help(argv[1])
            if command_help:
                return command_help
        return _takyon_help()

    if len(argv) >= 2 and argv[1].lower() in {"help", "-h", "--help"}:
        command_help = _format_control_command_help(command)
        if command_help:
            return command_help
        raise SystemExit(_takyon_help().replace("/takyon", "takyon"))

    if command == "ceo":
        return _format_ceo_focus(None, store, model)

    if command in {"shell", "interactive"}:
        if raw_json or not sys.stdin.isatty():
            return _takyon_help().replace("/takyon", "takyon")
        _interactive_shell(
            initial_business=argv[1] if len(argv) >= 2 else None,
            model=model or os.getenv("TAKYON_MODEL", ""),
            max_turns=int(max_turns or 30),
        )
        return None

    if command in {"commands", "skills", "harness"}:
        return _format_harness_commands()

    if command == "setup":
        return _setup_paths(store)

    if command == "model":
        if len(argv) >= 3 and argv[1] in {"set", "use"}:
            if len(argv) < 4:
                raise SystemExit("usage: takyon model set <provider> <model>")
            return _write_model_config(store, argv[2], argv[3])
        if len(argv) >= 3:
            return _write_model_config(store, argv[1], argv[2])
        return _format_model_config(store)

    if command == "secret":
        return _secret_command(store, argv)

    if command == "connect":
        return "Connector setup is handled by provider-specific skills/tools. Use `takyon secret set KEY VALUE` for credentials and keep business state in Hermes Takyon."

    if command in {"app-server", "api"}:
        from .app_api import run_app_api_server

        host = argv[1] if len(argv) >= 2 else "127.0.0.1"
        port = int(argv[2]) if len(argv) >= 3 else 8787
        run_app_api_server(host=host, port=port)
        return None

    if command in {"businesses", "business", "list"}:
        return store.read(scope="global", query="list_businesses")

    if command == "delete":
        parsed_delete = _parse_business_delete_args(argv)
        slug = str(parsed_delete["business"])
        return store.commit(
            scope=_scope_for_business(slug),
            operations=[{"action": "business.delete", **parsed_delete}],
            idempotency_key=_idempotency_key(
                "operator-business-delete-v1",
                slug,
                parsed_delete["confirm"],
                parsed_delete["delete_files"],
                parsed_delete["delete_cron"],
                parsed_delete["delete_domains"],
                ",".join(parsed_delete["subdomains"]),
            ),
            reason="operator requested business deletion" if parsed_delete["confirm"] else "operator previewed business deletion",
            actor="operator",
        )

    if command == "upgrade":
        parsed_upgrade = _parse_upgrade_args(argv)
        return upgrade_businesses(
            store=store,
            businesses=parsed_upgrade["businesses"],
            dry_run=bool(parsed_upgrade["dry_run"]),
        )

    if command == "registry":
        raise SystemExit("takyon registry was removed. Takyon bundled skills sync automatically on startup and the Hermes skills index rebuilds at runtime.")

    if command in {"status"}:
        if len(argv) < 2:
            raise SystemExit("usage: takyon status <business>")
        slug = _slugify(argv[1])
        return store.read(scope=_scope_for_business(slug), query="summary")

    if command == "pulse":
        if len(argv) < 2:
            raise SystemExit("usage: takyon pulse <business>")
        slug = _slugify(argv[1])
        return store.calculate_pulse(slug)

    if command == "test":
        if len(argv) < 2:
            raise SystemExit("usage: takyon test <business> on|off|status")
        slug = _slugify(argv[1])
        mode_arg = (argv[2] if len(argv) >= 3 else "status").strip().lower()
        if mode_arg in {"status", "show"}:
            data = store.read(scope=_scope_for_business(slug), query="summary")
            business = data.get("business") or {}
            return {"success": True, "business": business, "mode": business.get("mode") or "live"}
        mode = "test" if mode_arg in {"on", "test"} else "live" if mode_arg in {"off", "live"} else ""
        if not mode:
            raise SystemExit("usage: takyon test <business> on|off|status")
        return store.commit(
            scope=_scope_for_business(slug),
            operations=[{"action": "business.mode.set", "business": slug, "mode": mode}],
            idempotency_key=_idempotency_key("operator-test-mode", slug, mode),
            reason="operator set business test mode",
            actor="operator",
        )

    if command == "focus":
        if len(argv) < 2:
            raise SystemExit("usage: takyon focus <business> marketing|product|all|status")
        slug = _slugify(argv[1])
        focus_arg = (argv[2] if len(argv) >= 3 else "status").strip().lower()
        if focus_arg in {"status", "show"}:
            data = store.read(scope=_scope_for_business(slug), query="summary")
            business = data.get("business") or {}
            return {"success": True, "business": business, "work_focus": business.get("work_focus") or "all"}
        focus = _normalize_work_focus(focus_arg)
        return store.commit(
            scope=_scope_for_business(slug),
            operations=[{"action": "business.focus.set", "business": slug, "work_focus": focus}],
            idempotency_key=_idempotency_key("operator-work-focus", slug, focus),
            reason="operator set business work focus",
            actor="operator",
        )

    if command == "auto":
        raise SystemExit('takyon auto was folded into creation. Use: takyon create [--test] [--schedule "every 6h"] <business> <goal>')

    if command in {"files", "workspace"}:
        if len(argv) < 2:
            raise SystemExit(f"usage: takyon {command} <business> [path]")
        slug = _slugify(argv[1])
        return store.read(scope=_scope_for_business(slug), query="list_files", path=argv[2] if len(argv) >= 3 else ".")

    if command == "read":
        if len(argv) < 3:
            raise SystemExit("usage: takyon read <business> <path>")
        slug = _slugify(argv[1])
        return store.read(scope=_scope_for_business(slug), query="read_file", path=argv[2])

    if command in {"jobs", "capabilities", "caps"}:
        if len(argv) < 2:
            raise SystemExit(f"usage: takyon {command} <business>")
        slug = _slugify(argv[1])
        if command == "jobs":
            return store.read(scope=_scope_for_business(slug), query="jobs")
        return {
            "success": True,
            "business": slug,
            "message": "Takyon capabilities come from Hermes skills plus business_* tool gates. Skill-specific API readiness is declared in skill frontmatter.",
            "skills": [
                {
                    "name": item.get("name"),
                    "description": item.get("description"),
                }
                for item in _takyon_skill_entries()
            ],
        }

    if command in {"campaigns", "workspaces"}:
        if len(argv) < 2:
            raise SystemExit("usage: takyon campaigns <business>")
        slug = _slugify(argv[1])
        data = store.read(scope=_scope_for_business(slug), query="summary")
        workspaces = [
            item for item in data.get("workspaces", [])
            if str(item.get("path", "")).startswith(("distribution/", "campaigns/"))
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
        cron_result = store.commit(
            scope=_scope_for_business(slug),
            operations=[{"action": "cron.ensure_ceo_wakeup", "business": slug, "schedule": schedule}],
            idempotency_key=_idempotency_key("operator-wake", slug, schedule, uuid.uuid4().hex),
            reason="operator requested immediate CEO wake",
            actor="operator",
        )
        cron_job = ""
        for item in cron_result.get("results") or []:
            if isinstance(item, dict) and item.get("action") == "cron.ensure_ceo_wakeup":
                cron_job = str(item.get("cron_job") or "")
                break
        if not cron_job:
            from cron.jobs import list_jobs

            name = f"takyon-ceo:{slug}"
            existing = next((job for job in list_jobs(include_disabled=True) if job.get("name") == name), None)
            cron_job = str((existing or {}).get("id") or "")

        trigger_result: dict[str, Any] = {
            "action": "cron.trigger_ceo_wakeup",
            "business": slug,
            "cron_job": cron_job,
            "schedule": schedule,
            "triggered": False,
            "tick_ran": 0,
        }
        if store._work_requests_table() == "business_work_requests":
            wake_result = _run_pg_ceo_wake_once(store, slug)
            trigger_result["triggered"] = wake_result.get("status") in {"completed", "blocked", "failed", "running", "queued"}
            trigger_result["job"] = wake_result
            if wake_result.get("status") not in {"completed", "queued", "running"} and wake_result.get("error"):
                trigger_result["error"] = wake_result.get("error")
        elif cron_job:
            from cron.jobs import trigger_job
            from cron.scheduler import tick

            triggered = trigger_job(cron_job)
            trigger_result["triggered"] = bool(triggered)
            if triggered:
                trigger_result["tick_ran"] = tick(verbose=False)
            else:
                trigger_result["error"] = "scheduled CEO cron job could not be reopened"
        else:
            trigger_result["error"] = "no CEO cron job was returned after scheduling"

        return {
            "success": bool(cron_result.get("success")) and bool(trigger_result.get("triggered")),
            "results": [
                *(cron_result.get("results") or []),
                trigger_result,
            ],
        }

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

    if command in {"init", "create", "build"}:
        auto_default = command in {"create", "build"}
        slug, raw_name, goal, mode, schedule_arg, auto_start, no_auto = _parse_business_start_args(
            argv,
            usage=f'usage: takyon {command} [--test|--live] [--no-auto] [--schedule "every 6h"] <business> [goal text]',
            auto_default=auto_default,
        )
        config = _read_model_config(store)
        if auto_start and not no_auto:
            _require_agent_model_config(config, model_override=model)
        auto_wake = _config_bool(config.get("auto_schedule_ceo_on_create"), default=False)
        schedule = schedule_arg or (config.get("default_ceo_schedule") or "every 6h").strip()
        should_schedule = bool(schedule_arg) or (not no_auto and (auto_start or auto_wake))
        upsert_op: dict[str, Any] = {"action": "business.upsert", "business": slug, "name": raw_name, "goal": goal, "mode": mode}
        business_result = store.commit(
            scope=_scope_for_business(slug),
            operations=[upsert_op],
            idempotency_key=_idempotency_key("operator-init-v6", slug, mode or "keep", goal),
            reason="operator initialized business",
            actor="operator",
        )
        if not should_schedule:
            return business_result
        cron_result = store.commit(
            scope=_scope_for_business(slug),
            operations=[{"action": "cron.ensure_ceo_wakeup", "business": slug, "schedule": schedule}],
            idempotency_key=_idempotency_key("operator-init-wake-v3", slug, schedule),
            reason="operator initialized business CEO wake loop",
            actor="operator",
        )
        if auto_start:
            active = store.read(scope=_scope_for_business(slug), query="summary")
            active_mode = str((active.get("business") or {}).get("mode") or mode or "live")
            instruction = _business_bootstrap_instruction(slug, goal, active_mode)
            agent_response = _run_agent(
                _operator_context_message(instruction, slug),
                model=model or os.getenv("TAKYON_MODEL", ""),
                max_turns=int(max_turns or 30),
                show_activity=show_activity,
                show_indicator=show_indicator,
                shell_history=shell_history,
                operator_user_id=resolved_operator_user_id,
                current_business=slug,
            )
            return {
                "success": True,
                "business": slug,
                "mode": active_mode,
                "schedule": schedule,
                "init": business_result,
                "wake": cron_result,
                "agent_response": agent_response,
            }
        return {
            "success": True,
            "results": [
                *(business_result.get("results") or []),
                *(cron_result.get("results") or []),
            ],
        }

    if command == "budget":
        raise SystemExit(
            "legacy business budget caps were removed. Use the product usage budget rail instead."
        )

    if command == "memory":
        subcommand = argv[1] if len(argv) >= 2 else "list"
        if subcommand == "list":
            if len(argv) < 3:
                raise SystemExit("usage: takyon memory list <business>")
            slug = _slugify(argv[2])
            return store.read(scope=_scope_for_business(slug), query="list_files", path="research")
        if subcommand == "record":
            if len(argv) < 4:
                raise SystemExit("usage: takyon memory record <business> <text>")
            slug = _slugify(argv[2])
            content = "\n\n" + " ".join(argv[3:]).strip()
            return store.commit(
                scope=_scope_for_business(slug),
                operations=[{"action": "memory.write", "path": "operator-notes.md", "content": content, "mode": "append"}],
                idempotency_key=_idempotency_key("operator-memory", slug, hashlib.sha256(content.encode("utf-8")).hexdigest()),
                reason="operator recorded memory",
                actor="operator",
            )
        raise SystemExit("usage: takyon memory list|record ...")

    if command == "command":
        if len(argv) < 3:
            raise SystemExit("usage: takyon command <business> <harness-command> [args...]")
        slug = _slugify(argv[1])
        harness_command = _get_harness_command(argv[2])
        if not harness_command:
            raise SystemExit(f"unknown harness command: {argv[2]}")
        message = _render_harness_command(harness_command, business=slug, args=argv[3:], store=store)
        return _run_agent(
            message,
            model=model or os.getenv("TAKYON_MODEL", ""),
            max_turns=int(max_turns or 30),
            show_activity=show_activity,
            show_indicator=show_indicator,
            shell_history=shell_history,
            operator_user_id=resolved_operator_user_id,
            current_business=slug,
        )

    if command in {"run", "goal", "/goal"}:
        if len(argv) < 2:
            raise SystemExit(f"usage: takyon {command} <business> [instruction]")
        slug = _slugify(argv[1])
        instruction = " ".join(argv[2:]).strip() or "Continue from current business evidence."
        return _run_agent(
            _operator_context_message(instruction, slug),
            model=model or os.getenv("TAKYON_MODEL", ""),
            max_turns=int(max_turns or 30),
            show_activity=show_activity,
            show_indicator=show_indicator,
            shell_history=shell_history,
            operator_user_id=resolved_operator_user_id,
            current_business=slug,
        )

    if command == "gc":
        days = int(argv[1]) if len(argv) >= 2 and argv[1].isdigit() else 90
        confirm = any(item.lower() in {"confirm", "--confirm", "yes"} for item in argv[2:])
        return store.commit(
            scope="global",
            operations=[{"action": "maintenance.gc", "older_than_days": days, "confirm": confirm}],
            idempotency_key=_idempotency_key("operator-gc", days, confirm),
            reason="operator requested Takyon maintenance GC",
            actor="operator",
        )

    message = " ".join(argv).strip()
    if not message:
        raise SystemExit("empty Takyon command")
    return _run_agent(
        _operator_context_message(message, None),
        model=model or os.getenv("TAKYON_MODEL", ""),
        max_turns=int(max_turns or 30),
        show_activity=show_activity,
        show_indicator=show_indicator,
        shell_history=shell_history,
        operator_user_id=resolved_operator_user_id,
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
            show_indicator=not raw_json,
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
    removed_message = _REMOVED_COMMANDS.get(command)
    if removed_message:
        return removed_message
    if command in {"help", "-h", "--help"}:
        return _takyon_help()

    if command == "ceo":
        return _format_ceo_focus(None, TakyonStore(), os.getenv("TAKYON_MODEL", ""))

    if command == "skill":
        if len(argv) < 2:
            return "Usage: /takyon skill <skill-name> <instruction>"
        skill_ref = _resolve_skill_reference(argv[1])
        if not skill_ref:
            return f"Unknown Takyon skill for /takyon: {argv[1]}"
        return _queue_skill_invocation(ctx, skill_ref[1], " ".join(argv[2:]).strip())

    if command in _local_command_names():
        try:
            return _format_slash_value(run_takyon_command(argv))
        except SystemExit as exc:
            return str(exc)
        except Exception as exc:
            return f"Takyon error: {exc}"

    skill_ref = _resolve_skill_reference(command)
    if skill_ref:
        return _queue_skill_invocation(ctx, skill_ref[1], " ".join(argv[1:]).strip())

    return _queue_ceo_invocation(ctx, " ".join(argv).strip())


if __name__ == "__main__":
    main()
