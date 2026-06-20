"""Terminal entrypoint for the Takyon plugin."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from .core import (
    TakyonError,
    TakyonStore,
    _company_base_domain,
    _is_reserved_public_subdomain,
    _normalize_work_focus,
    _slugify,
    load_takyon_env,
    upgrade_businesses,
)


_CEO_PROMPT_PATH = Path(__file__).parent / "prompts" / "ceo.md"
_TAKYON_SKILL_ALIASES = {
    "market-research": "takyon-market-research",
    "build-product": "takyon-build-product",
    "app-runtime": "takyon-app-runtime",
    "distribution": "takyon-distribution",
    "business-pulse": "takyon-business-metrics",
    "business-metrics": "takyon-business-metrics",
}
_TAKYON_SKILL_PREFIX = "takyon-"
_DEFAULT_BOOTSTRAP_MAX_TURNS = 30
_CREATE_NAME_LLM_PROMPT = (
    "Choose the canonical initial product or company name from the user's idea. "
    "If the user explicitly gives or strongly implies a name, use that exact name. "
    "Only invent a concise new name when the idea does not already imply one. "
    "Return only the name text, with no quotes, JSON, explanation, or extra words."
)


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
        ledger = value.get("ledger") or []
        lines = [
            f"Ledger for business:{business.get('slug') or '<unknown>'}:",
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
            raw_mode = value.get("mode") or business.get("mode") or "live"
            mode = "live" if str(raw_mode).strip().lower() != "live" else "live"
        else:
            slug = str(business or "<unknown>")
            raw_mode = value.get("mode") or "live"
            mode = "live" if str(raw_mode).strip().lower() != "live" else "live"
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
            raw_mode = item.get("mode") or "live"
            mode = "live" if str(raw_mode).strip().lower() != "live" else "live"
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
            lines.append("Mode: live")
        if business.get("work_focus"):
            lines.append(f"Work focus: {business.get('work_focus')}")
        if business.get("goal"):
            lines.append(f"Goal: {business.get('goal')}")
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
        return f"business:{business or item.get('business')} mode -> live"
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
        return f"app plan policy updated for business:{business}{suffix}"
    if action in {"app.customer.upsert", "app.entitlement.upsert"} and business:
        return f"app customer/entitlement state updated for business:{business}"
    if action == "app.usage.record" and business:
        return f"app usage recorded for business:{business}"
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


_CREATE_NAME_LEADING_VERBS = {
    "build",
    "create",
    "make",
    "start",
    "launch",
}
_CREATE_NAME_LEADING_FILLERS = {
    "a",
    "an",
    "the",
}
_CREATE_NAME_STOP_WORDS = {
    "for",
    "that",
    "which",
    "with",
    "using",
    "via",
    "to",
}
_CREATE_NAME_GENERIC_NOUNS = {
    "app",
    "application",
    "business",
    "company",
    "platform",
    "product",
    "service",
    "site",
    "startup",
    "store",
    "tool",
}


def _collapse_whitespace(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _format_create_name_token(token: str) -> str:
    raw = str(token or "").strip()
    if not raw:
        return ""
    pieces: list[str] = []
    for part in re.split(r"([/+_-])", raw):
        if not part:
            continue
        if part in {"/", "+", "_", "-"}:
            pieces.append(part)
            continue
        if part.isupper() and len(part) <= 4:
            pieces.append(part)
            continue
        if "'" in part:
            left, right = part.split("'", 1)
            left = f"{left[:1].upper()}{left[1:].lower()}" if left else ""
            part = f"{left}'{right.lower()}"
        else:
            part = f"{part[:1].upper()}{part[1:].lower()}"
        pieces.append(part)
    return "".join(pieces)


def _display_name_from_slug(slug: str) -> str:
    parts = [part for part in re.split(r"[-_]+", _slugify(slug)) if part]
    name = " ".join(_format_create_name_token(part) for part in parts if part)
    return name or _slugify(slug)


def _extract_create_name_candidate(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict):
            text = str(data.get("name") or "").strip()
    text = text.splitlines()[0].strip()
    if ":" in text:
        prefix, suffix = text.split(":", 1)
        if prefix.strip().lower() in {"name", "business name", "company name", "product name"}:
            text = suffix.strip()
    if " - " in text:
        prefix, _suffix = text.split(" - ", 1)
        if prefix.strip():
            text = prefix.strip()
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
    text = text.strip("`\"' ")
    text = re.sub(r"[.。,:;!?]+$", "", text).strip()
    return _collapse_whitespace(text)


def _create_name_call_estimate_cents() -> int:
    raw = str(os.getenv("TAKYON_CREATE_NAME_ESTIMATE_CENTS") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 1


def _create_name_call_actual_cents(
    response: Any,
    *,
    model: str,
    runtime: dict[str, Any],
    reserved_cents: int,
) -> int:
    response_usage = getattr(response, "usage", None)
    if not response_usage:
        return max(0, int(reserved_cents or 0))
    try:
        from agent.usage_pricing import estimate_usage_cost, normalize_usage

        usage = normalize_usage(
            response_usage,
            provider=str(runtime.get("provider") or ""),
            api_mode=str(runtime.get("api_mode") or ""),
        )
        cost = estimate_usage_cost(
            model,
            usage,
            provider=str(runtime.get("provider") or ""),
            base_url=str(runtime.get("base_url") or ""),
        )
        if cost.amount_usd is not None:
            return max(0, int(round(float(cost.amount_usd) * 100)))
        if cost.status == "included":
            return 0
    except Exception:
        pass
    return max(0, int(reserved_cents or 0))


def _derive_name_from_goal_with_llm(goal: str, *, operator_user_id: str | None = None) -> str:
    load_takyon_env()
    from agent.auxiliary_client import call_llm
    from takyon_cli.runtime_provider import resolve_runtime_provider

    model_config = _read_model_config(TakyonStore())
    resolved_model = _require_agent_model_config(model_config)
    configured_provider = str(model_config.get("provider") or "").strip()
    runtime = resolve_runtime_provider(
        requested=configured_provider or None,
        target_model=resolved_model,
    )
    resolved_operator_user_id = _resolved_operator_user_id(operator_user_id)
    reservation_key = ""
    reserved_cents = 0
    response = None
    try:
        if resolved_operator_user_id:
            reservation_key, reserved_cents = _operator_budget_reserve(
                operator_user_id=resolved_operator_user_id,
                business_slug=None,
                reservation_key=_idempotency_key("create-name", uuid.uuid4().hex),
                estimate_cents=_create_name_call_estimate_cents(),
            )
        response = call_llm(
            provider=str(runtime.get("provider") or configured_provider or "").strip() or None,
            model=resolved_model,
            main_runtime=runtime,
            messages=[
                {"role": "system", "content": _CREATE_NAME_LLM_PROMPT},
                {"role": "user", "content": goal},
            ],
            temperature=0.0,
            max_tokens=32,
            timeout=20.0,
        )
        content = ""
        try:
            content = str(response.choices[0].message.content or "")
        except Exception:
            content = ""
        candidate = _extract_create_name_candidate(content)
        if not candidate:
            raise TakyonError("model did not return a usable business name")
        return candidate
    finally:
        if reservation_key:
            _operator_budget_finalize(
                operator_user_id=resolved_operator_user_id,
                business_slug=None,
                reservation_key=reservation_key,
                reserved_cents=reserved_cents,
                actual_cents=_create_name_call_actual_cents(
                    response,
                    model=resolved_model,
                    runtime=runtime,
                    reserved_cents=reserved_cents,
                )
                if response is not None
                else 0,
            )


def _derive_name_from_goal(goal: str) -> str:
    text = _collapse_whitespace(goal)
    if not text:
        raise TakyonError("business name or goal is required")
    candidate = text
    for separator in (" -- ", " - ", " — ", " – ", ": "):
        if separator in candidate:
            prefix = candidate.split(separator, 1)[0].strip()
            if prefix:
                candidate = prefix
                break
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'/_+-]*", candidate)
    if not tokens:
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'/_+-]*", text)
    selected: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if not selected and lowered in _CREATE_NAME_LEADING_VERBS:
            continue
        if not selected and lowered in _CREATE_NAME_LEADING_FILLERS:
            continue
        if selected and lowered in (_CREATE_NAME_STOP_WORDS | _CREATE_NAME_GENERIC_NOUNS):
            break
        if lowered in _CREATE_NAME_GENERIC_NOUNS and not selected:
            continue
        selected.append(token)
        if len(selected) >= 3:
            break
    if not selected:
        raise TakyonError("could not derive a business name from the goal")
    name = " ".join(_format_create_name_token(token) for token in selected if token)
    name = _collapse_whitespace(name)
    if not name:
        raise TakyonError("could not derive a business name from the goal")
    return name


def _resolve_create_identity(name: str, goal: str, slug_hint: str = "") -> tuple[str, str]:
    explicit_name = _collapse_whitespace(name)
    if explicit_name:
        return explicit_name, _preferred_public_business_slug(explicit_name)
    goal_text = _collapse_whitespace(goal)
    if goal_text:
        resolved_name = _derive_name_from_goal(goal_text)
        return resolved_name, _preferred_public_business_slug(resolved_name)
    slug_seed = _collapse_whitespace(slug_hint)
    if slug_seed:
        slug = _preferred_public_business_slug(slug_seed)
        return _display_name_from_slug(slug), slug
    raise TakyonError("business name or goal is required")


def _preferred_public_business_slug(value: str) -> str:
    """Prefer a slug that won't collide with reserved Four Manifold infra hosts."""
    slug = _slugify(value)
    if not _is_reserved_public_subdomain(slug):
        return slug

    max_len = 80
    for suffix in ("site", "app", "co", "lab"):
        trimmed = slug[: max_len - len(suffix) - 1].rstrip("-_")
        candidate = _slugify(f"{trimmed}-{suffix}" if trimmed else suffix)
        if not _is_reserved_public_subdomain(candidate):
            return candidate

    counter = 2
    while True:
        suffix = f"site-{counter}"
        trimmed = slug[: max_len - len(suffix) - 1].rstrip("-_")
        candidate = _slugify(f"{trimmed}-{suffix}" if trimmed else suffix)
        if not _is_reserved_public_subdomain(candidate):
            return candidate
        counter += 1


def _resolve_free_public_business_slug(store: "TakyonStore", value: str) -> str:
    """Return the preferred public slug, auto-incrementing past any existing business.

    The preferred slug already avoids reserved infra subdomains. On top of that, when
    the resolved slug already names a created business, append ``-2``, ``-3``, ... until
    a free, non-reserved slug is found so a duplicate create succeeds under a new slug
    instead of hard-failing on collision. Bounded and slug-length clamped.
    """
    preferred = _preferred_public_business_slug(value)
    if not _business_exists(store, preferred):
        return preferred

    max_len = 80
    base = preferred
    for counter in range(2, 1000):
        suffix = f"-{counter}"
        trimmed = base[: max_len - len(suffix)].rstrip("-_")
        candidate = _slugify(f"{trimmed}{suffix}" if trimmed else f"site{suffix}")
        if _is_reserved_public_subdomain(candidate):
            continue
        if not _business_exists(store, candidate):
            return candidate
    # Exhausted the bounded range; fall back to a unique suffix so creation never blocks.
    unique = _slugify(f"{base[: max_len - 9].rstrip('-_')}-{uuid.uuid4().hex[:6]}")
    return unique or _slugify(f"site-{uuid.uuid4().hex[:6]}")


def _resolve_dashboard_create_identity(
    name: str,
    goal: str,
    slug_hint: str = "",
    *,
    operator_user_id: str | None = None,
    store: "TakyonStore | None" = None,
) -> tuple[str, str]:
    # BUG-006: the dashboard caller must receive the SAME slug the `create`
    # command will actually persist. `create` auto-increments past a slug
    # collision to the next free slug (`...-2`), so resolving only the preferred
    # slug here strands the dashboard (its strict durable-business check looks up
    # the pre-collision slug) or silently attaches to a pre-existing wrong
    # business. When a store is supplied, resolve the FREE public slug so both
    # the dashboard and the create subprocess agree on the final slug.
    def _final_slug(seed_slug: str) -> str:
        if store is not None:
            return _resolve_free_public_business_slug(store, seed_slug)
        return _preferred_public_business_slug(seed_slug)

    explicit_name = _collapse_whitespace(name)
    if explicit_name:
        return explicit_name, _final_slug(explicit_name)
    goal_text = _collapse_whitespace(goal)
    if goal_text:
        try:
            resolved_name = _derive_name_from_goal_with_llm(
                goal_text,
                operator_user_id=operator_user_id,
            )
        except TakyonError as exc:
            if "operator budget exhausted" in str(exc).lower():
                raise
        except Exception:
            resolved_name = ""
        else:
            resolved_name = _collapse_whitespace(resolved_name)
            if resolved_name:
                return resolved_name, _final_slug(resolved_name)
        fallback_name, fallback_slug = _resolve_create_identity("", goal_text, slug_hint)
        return fallback_name, _final_slug(fallback_slug)
    fallback_name, fallback_slug = _resolve_create_identity("", "", slug_hint)
    return fallback_name, _final_slug(fallback_slug)


@contextlib.contextmanager
def _business_workspace_execution_context(
    slug: str,
    *,
    operator_user_id: str | None = None,
    sync_on_exception: bool = False,
):
    from .core import TakyonStore, _mounted_canonical_business_workspace

    load_takyon_env()
    store = TakyonStore(operator_user_id=operator_user_id)
    with _mounted_canonical_business_workspace(
        store,
        slug,
        owner_label=str(operator_user_id or slug),
    ) as (workspace_home, _backend, _base_revision):
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
    explicit_name: str | None = None
    auto_start = auto_default
    no_auto = False
    clean: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--test":
            raise SystemExit("test mode is disabled; remove --test. All businesses run live.")
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
        elif token == "--name":
            index += 1
            if index >= len(tokens):
                raise SystemExit(usage)
            explicit_name = str(tokens[index] or "").strip() or None
        elif token in {"-h", "--help", "help"}:
            raise SystemExit(usage)
        elif token.startswith("--"):
            raise SystemExit(f"unknown create flag {token!r}\n{usage}")
        else:
            clean.append(token)
        index += 1
    if not clean:
        raise SystemExit(usage)
    slug_token = clean[0]
    raw_name = explicit_name or slug_token
    slug = _slugify(slug_token)
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
    """Resolve the acting operator: explicit argument, then the per-session identity a parent
    process injected (TAKYON_SESSION_USER_ID — e.g. the detached-command subprocess spawned by the
    TUI gateway), then — ONLY on planes that have not declared per-session identity — the legacy
    process-global TAKYON_OPERATOR_USER_ID convenience (see core.operator_identity_mode)."""
    explicit = str(operator_user_id or "").strip()
    if explicit:
        return explicit
    session_user = str(os.getenv("TAKYON_SESSION_USER_ID") or "").strip()
    if session_user:
        return session_user
    from .core import operator_identity_mode

    if operator_identity_mode():
        return ""
    return str(os.getenv("TAKYON_OPERATOR_USER_ID") or "").strip()


# Authoritative operator-wallet gate for company creation: creating a business requires STRICTLY
# more than this percent of the plan-funded period allowance remaining, and consumes exactly this
# percent of the period allowance on create. Backend source of truth — never trust the client.
_CREATE_ALLOWANCE_GATE_PERCENT = 3


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


class InsufficientOperatorBalance(TakyonError):
    """Company creation was refused because the acting operator's plan-funded allowance is at or
    below the create floor (>3% of the period allowance remaining). Carries the exact figures so the
    gateway can build a precise 402/4030 block without leaking anything else. Distinct error type so
    the create handler maps it to the balance-block code rather than a generic create failure."""

    def __init__(
        self,
        *,
        spendable_cents: int,
        allowance_remaining_cents: int,
        allowance_included_cents: int = 0,
        percent_remaining: float = 0.0,
        required_percent: float = _CREATE_ALLOWANCE_GATE_PERCENT,
    ) -> None:
        self.spendable_cents = int(spendable_cents)
        self.allowance_remaining_cents = int(allowance_remaining_cents)
        self.allowance_included_cents = int(allowance_included_cents)
        self.percent_remaining = float(percent_remaining)
        self.required_percent = float(required_percent)
        super().__init__(
            "insufficient_balance: company creation requires "
            f">{self.required_percent:g}% remaining "
            f"(have {self.percent_remaining:g}%, allowance remaining "
            f"{self.allowance_remaining_cents}c of {self.allowance_included_cents}c)"
        )


def _operator_create_balance_preflight(
    operator_user_id: str | None,
    *,
    business_slug: str | None = None,
) -> None:
    """Authoritatively gate AND charge company creation on the operator wallet (the plan-funded
    allowance). Backend source of truth — never trust the client. The CEO bootstrap of a new company
    spends real provider money on the operator billing rail, so building a company for an operator
    who cannot pay is an ungated-spend hole. This is the single create chokepoint both the shell
    /create and the dashboard create RPC funnel through.

    Rules (atomic, fail-closed):
    - REQUIRE ``allowance_percent_remaining > 3`` — the SAME percent the dashboard surfaces
      (``remaining / included * 100``, web_server.py operator-account payload). At or below 3% ⇒
      refuse with ``InsufficientOperatorBalance`` (mapped to the 4030 balance block).
    - DECREMENT 3% of the period allowance (``included * 3 / 100`` cents) on create by consuming it
      through the billing rail (reserve → settle), so the wallet authoritatively drops by 3% per
      created company. Idempotent on the business slug so a retried create never double-charges.

    Fail-OPEN only for genuinely identity-less / non-Postgres dev runs, exactly like
    ``_operator_budget_reserve``: with no resolved operator identity or no Postgres control plane
    there is no billing account to read and local development must not be blocked. On the Postgres
    plane WITH a resolved operator, a missing billing account or a zero/empty allowance is treated as
    unfunded, so it fails CLOSED — assume the caller may be trying to create without paying (§3)."""
    from .core import _db_backend

    user_id = _resolved_operator_user_id(operator_user_id)
    if not user_id or _db_backend() != "postgres":
        return  # no billing plane to gate on (dev / identity-less) — do not block local creation

    import psycopg

    try:
        from . import billing
        from .runtime_app import resolve_database_url
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon import billing
        from plugins.takyon.runtime_app import resolve_database_url

    conn = psycopg.connect(resolve_database_url(), autocommit=True)
    try:
        try:
            balances = billing.get_billing_balances(conn, user_id)
        except billing.NoBillingAccount as exc:
            # On the Postgres plane a resolved operator with NO billing account has no funding ⇒
            # fail closed. § 3 (assume evil): never build a company for an operator with no
            # provable balance.
            raise InsufficientOperatorBalance(
                spendable_cents=0,
                allowance_remaining_cents=0,
                allowance_included_cents=0,
                percent_remaining=0.0,
            ) from exc
        allowance_included = max(0, int(balances.allowance_included_cents))
        allowance_remaining = max(0, int(balances.allowance_remaining_cents))
        # A zero/empty period allowance can never clear the >3% floor — fail closed without a
        # division-by-zero. percent_remaining is the dashboard-surfaced figure.
        percent_remaining = (
            (allowance_remaining / allowance_included) * 100.0
            if allowance_included > 0
            else 0.0
        )
        # Require STRICTLY more than 3% remaining. Compare on cents (remaining*100 > 3*included)
        # to avoid float rounding at the boundary.
        if allowance_included <= 0 or (
            allowance_remaining * 100 <= _CREATE_ALLOWANCE_GATE_PERCENT * allowance_included
        ):
            raise InsufficientOperatorBalance(
                spendable_cents=allowance_remaining,
                allowance_remaining_cents=allowance_remaining,
                allowance_included_cents=allowance_included,
                percent_remaining=round(percent_remaining, 1),
            )
        # Two callers funnel here for one create: the dashboard create RPC runs a slug-LESS pre-check
        # (operator known, slug not resolved yet), then the create chokepoint inside run_takyon_command
        # runs the REAL gate WITH the resolved slug. Only the slug-bearing call performs the
        # authoritative DECREMENT; the slug-less pre-check stays a read-only gate (no side effect) so
        # it cannot double-charge. Both still fail closed on the >3% floor above.
        if not str(business_slug or "").strip():
            return
        # Authoritative decrement: consume 3% of the period allowance on create. Round so a tiny
        # allowance still charges at least 1c (never a free create once past the gate).
        charge_cents = max(
            1, (allowance_included * _CREATE_ALLOWANCE_GATE_PERCENT + 99) // 100
        )
        # Idempotent per create: keyed on the resolved slug so a retried create reuses the same
        # reservation and never double-charges. reserve → settle drives allowance_used up by
        # charge_cents.
        reservation_key = _idempotency_key(
            "operator-create-charge", str(business_slug).strip(), str(_CREATE_ALLOWANCE_GATE_PERCENT)
        )
        try:
            # This charge is OPERATOR-scoped (the operator's plan allowance), and it runs at the
            # create chokepoint BEFORE the businesses row is committed, so it must NOT tag the
            # reservation with the new slug — billing_entries.business_slug carries an FK to
            # businesses(slug), and that row does not exist yet. The slug is already baked into the
            # idempotency key for create-specific replay safety.
            res = billing.reserve(
                conn,
                user_id,
                charge_cents,
                reservation_key,
                business_slug=None,
            )
        except billing.InsufficientBalance as exc:
            # Race: allowance fell below the charge between the read and the reserve. Fail closed.
            raise InsufficientOperatorBalance(
                spendable_cents=allowance_remaining,
                allowance_remaining_cents=allowance_remaining,
                allowance_included_cents=allowance_included,
                percent_remaining=round(percent_remaining, 1),
            ) from exc
        # Settle the full reservation at the held amount so the 3% is permanently consumed (not
        # released). Idempotent: a replayed key returns the same reservation and re-settling is a
        # no-op (first finalizer wins).
        billing.settle(conn, reservation_key, int(res.allowance_cents))
    finally:
        conn.close()


# Free starter creative credits granted to every new business on create so the bootstrap logo and
# first X post auto-run instead of failing closed on a 0-credit balance. 3 credits = X (1) + logo (2).
_BUSINESS_BOOTSTRAP_FREE_CREDITS = 3


def _seed_business_free_credits(slug: str) -> None:
    """Open the business creative-credit account and grant the free starter pack on create.

    Idempotent on the slug (``business_credits.grant_credits`` no-ops on a replayed idempotency_key),
    so a retried create never re-grants. Fail-open only for non-Postgres dev runs, where there is no
    creative-credit ledger to seed and local creation must not be blocked. This makes the bootstrap
    logo + first X auto-run; without it both fail closed on a zero credit balance."""
    from .core import _db_backend

    business_slug = str(slug or "").strip()
    if not business_slug or _db_backend() != "postgres":
        return  # no creative-credit ledger to seed (dev / non-Postgres)

    import psycopg

    try:
        from . import business_credits
        from .runtime_app import resolve_database_url
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon import business_credits
        from plugins.takyon.runtime_app import resolve_database_url

    conn = psycopg.connect(resolve_database_url(), autocommit=True)
    try:
        business_credits.open_business_credit_account(conn, business_slug)
        business_credits.grant_credits(
            conn,
            business_slug,
            _BUSINESS_BOOTSTRAP_FREE_CREDITS,
            idempotency_key=f"{business_slug}-bootstrap-free-seed",
            metadata={"reason": "bootstrap free starter (X+logo)"},
        )
    finally:
        conn.close()


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
            f"need {exc.estimate_cents}c, allowance {exc.allowance_available_cents}c"
        ) from exc
    finally:
        conn.close()
    return res.key, int(res.allowance_cents)


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
            overflow_reserved = int(overflow_res.allowance_cents)
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


def _business_bootstrap_instruction(
    slug: str,
    goal: str,
    active_mode: str,
    *,
    business_name: str = "",
) -> str:
    goal_text = goal or "Use current business state and evidence to define the business goal."
    effective_mode = "live" if str(active_mode or "").strip().lower() != "live" else "live"
    lines = [
        f"Bootstrap business:{slug} now.",
        "",
        "This is an operational create/build request. Execute immediately.",
        "Do not respond with instructions, checklists, or 'want me to start?'.",
        "",
        f"Canonical business name: {business_name or slug}",
        f"Business goal: {goal_text}",
        f"Mode: {effective_mode}",
        "",
        "## Execution rules",
        "",
        "Fresh create. Business state is empty.",
        "- Do NOT call business_read_business, business_read_file, or business_list_files before acting.",
        "- Do NOT call todo or update task lists at any point.",
        "- Do NOT call skills_list.",
        "- Load only takyon-market-research, takyon-brand-logo, takyon-x, and takyon-distribution via skill_view. Do not load any other skill during bootstrap.",
        "- After completing each step, move to the next immediately.",
        "- Use exactly the business name above. Do not invent a second company, umbrella brand, or product name.",
        "- Consumer voice: this bootstrap turn is shown live to the customer on the build screen and product chat. Write every visible sentence as a warm, high-level, business-focused update describing the BUSINESS work (researching the market, designing the product, putting the site online, drafting the launch post) — never the runtime plumbing.",
        "- Curated update channel: the customer sees ONLY the curated update you post with business_post_operator_update, never your raw assistant reasoning. Keep ALL planning, deliberation, tool choreography, and chain-of-thought internal. At the very start of this turn, call business_post_operator_update with a warm headline, a 1-2 sentence summary, and a milestones plan covering the steps below — e.g. {title: \"Research the market\", category: RESEARCH, status: running}, {title: \"Design and build the product site\", category: PRODUCT, status: queued}, {title: \"Put the launch post out\", category: LAUNCH, status: queued}. Re-post the update (flipping each milestone's status) as you complete research, then the product build, then the X post, and when anything blocks. The milestones become the customer's Tasks cards; do not narrate low-level tool calls yourself.",
        "- Never surface raw internal platform/tool/runtime strings in the visible reply. Do not quote TAKYON_* flags, docker path diagnostics, workspace-mode errors, or similar internals; summarize blockers in normal operator language instead.",
        "- Forbidden in any customer-visible sentence: \"bootstrap\", \"site worker\", \"scaffold\"/\"scaffolding\", \"upsert\"/\"upserted\", \"provision\"/\"provisioned\", \"app account\", \"workspace exists\"/\"workspace ready\" (say \"your company space\" instead), \"runtime\", \"surface contract\", \"app shell\", \"kit\", any tool name (business_upsert_*, business_claude_agent_task, etc.), and verbatim tool/web-access limitations like \"publicly cached\".",
        "- If a web or tool capability is limited, say it plainly to the customer, e.g. \"I'm working from the sources I can reach right now\", without naming the mechanism.",
        "- These forbidden terms apply only to the VISIBLE reply shown to the customer. The internal directives in this instruction (which deliberately use words like bootstrap, surface contract, scaffold, upsert, and tool names to steer you) are not customer-visible and stay as written.",
        "",
        "## Steps",
        "",
        "### 1. Minimal landing brief (from the idea alone — NO web research yet)",
        "Goal: get the customer a real, branded landing page live FAST. Derive the landing brief from the BUSINESS IDEA ALONE plus the provided design/style packs — do NOT do any web research before the first landing publishes.",
        "Do NOT load takyon-market-research in this step, and do NOT call web_search, web_extract, web_tools, business_web_search, Tavily, or any other live-evidence/market-research tool before the 2a landing pass. That web-extract pass is the slowest part of bootstrap and would delay the customer's first paint by minutes; it is deferred entirely to step 3 (after the landing is live).",
        "From the idea (and the canonical business name and goal above), reason out and pin down: the business name, a one-line tagline, the core value proposition, who the customer is (ICP / audience), the core problem the product solves, the offer, the brand tone, and one launch angle. This is straightforward derivation from the idea, not research — no sources are required for a truthful, branded landing.",
        "Write that into research/strategy.md as the initial landing brief. Mark it as the idea-only fast pass; you will deepen and source-back it in step 3 after the landing is live.",
        "Keep the landing TRUTHFUL: a landing built from the idea alone is fine, but do NOT fabricate statistics, customer counts, testimonials, named partners, awards, or evidence-backed claims you have not verified. Stick to the product's own value proposition and offer. Deeper, source-backed claims come later in step 3 and the X post.",
        "Stop as soon as you have enough of the brief for truthful, branded landing copy, then move straight to step 2. The deeper market/X research is deferred to step 3 so it overlaps the rest of bootstrap instead of blocking the first landing publish.",
        "",
        "### 2. Product surface + landing build (publish the landing FIRST)",
        "Call business_upsert_app_surface_contract with:",
        "- source_path: product/site",
        "- runtime_features: auth, account, profile, checkout",
        "- routes: / (landing page), /app (sign-in + subscription gate), and /app/profile (account page)",
        "",
        "This seeds the COMPLETE app kit up front (landing, the /app access shell, the /app/profile account page, support, and the shared auth/checkout/account rails). The two build passes below only change WHEN each screen is customized and published; they never change the final fileset. The end state must be the same complete app kit as a single-pass build.",
        "",
        "If the app shell is monthly paid, call business_upsert_app_plan for the canonical `monthly` plan before the site worker runs so the existing checkout rail has a real plan object to use.",
        "- Use the researched monthly price when it is already known.",
        "- Set `included_ai_budget_microusd` together with `price_cents`.",
        "- If pricing is not settled yet, keep the canonical starter monthly plan instead of leaving checkout planless.",
        "",
        "#### 2.0. Pin the idea-branded hero copy (so the published landing is already on-message)",
        "Capture the brand voice deterministically before the design pass. Call business_write_file with path `product/hero.json` and a JSON object drawn straight from the step-1 brief: {\"eyebrow\": <a 2-4 word kicker>, \"headline\": <the punchy one-line tagline>, \"subhead\": <one truthful sentence on the core value proposition>}. Keep it TRUTHFUL — no fabricated stats, counts, or claims. The 2a design pass below builds the real landing on-message from this brief. (Do NOT try to publish the seeded scaffold directly: the publish gate requires the rewritten/themed landing, which is the 2a pass.)",
        "",
        "#### 2a. Build and publish the landing page",
        "Now upgrade that fast first-paint into the full, polished, custom landing so the site looks bespoke, not templated. Call business_claude_agent_task with:",
        "- workspace: product/site",
        "- instruction: Use the pinned Vite scaffold materialized in the workspace as the runtime rail base. Keep the shared runtime wiring through `src/lib/takyon.ts` and `src/lib/hooks.ts` while making the landing page business-specific. Choose one coherent visual direction from the brief and the provided style packs, then follow it consistently without blending packs.",
        '- guidance_skills: ["claude-design", "claude-design-openai", "claude-design-stripe", "claude-design-superhuman", "claude-design-vibrant", "claude-design-doodle"] so the delegated site worker receives the shared design method plus the available shared style packs.',
        "- Scope this pass to ONLY the landing route `/`: customize `src/screens/landing.tsx` so it is a truthful, branded landing page. Do NOT edit `src/screens/app-layout.tsx`, `src/screens/app-home.tsx`, or `src/screens/profile.tsx` in this pass — those are customized in 2b.",
        "- Keep the shared Vite route skeleton and the seeded `/app`, `/app/profile`, and support routes intact; do not delete or stub any seeded screen. They stay as the seeded app kit until 2b refines them.",
        "- refresh_surface: true",
        "- max_turns: 24 — the landing pass edits ONLY `src/screens/landing.tsx`, so it needs far fewer turns than the full 2b app-shell pass. A tight budget keeps the customer's first paint fast; the runtime auto-escalates the cap and retries once on a genuine turn-cap hit, so a tight landing cap is self-healing, not a quality risk.",
        "- effort: low — the landing is ONE well-specified, design-pack-guided screen; low effort is sufficient and materially faster per turn. (Pass NOTHING here for 2b so the heavier app-shell pass keeps the default effort.)",
        "- timeout_ms: 480000 — an 8-minute ceiling so a wandering worker can never stall the customer's first paint for the full 20-minute default; the auto-escalation retry covers the rare genuine need.",
        "- model: PASS NOTHING — keep the default Sonnet model for the landing pass. The landing is the customer's first impression, so do NOT downgrade to a cheaper/faster model (e.g. Haiku) to shave time: landing QUALITY takes priority over speed, and a first paint in roughly five minutes is acceptable. The `max_turns: 24` and `effort: low` levers above keep this pass reasonably fast without lowering the model itself.",
        "",
        "This 2a pass with `refresh_surface: true` PUBLISHES AND SERVES the landing immediately on its own: the worker's `surface_refresh.publish.status` should come back `published` and the live site at the customer host serves the new landing right away, with the still-seeded real `/app` access shell shipping behind sign-in until 2b refines it. The landing does NOT wait for 2b to be served — confirm `surface_refresh.publish.status == \"published\"` and a real `public_url` in this pass's structured result before continuing.",
        "",
        "Inspect the structured result from this first business_claude_agent_task. Trust only its exact success/blocker and surface_refresh publish status. If the landing build or publish is blocked, record that exact blocker in research/strategy.md and stop bootstrap there; do not continue to Search Console, the logo, the rest of the app kit, or X.",
        "A `detached: true` result (status `queued` or `running`, with a re-attach note) is NOT a blocker and NOT a failure — the build is simply still running on the worker plane. Do NOT record it as a blocker and do NOT stop the bootstrap. Re-call business_claude_agent_task with the SAME workspace, instruction, and idempotency_key to re-attach and collect the published result; repeat until it returns either `surface_refresh.publish.status == \"published\"` (continue) or a real blocker (then stop). Only an explicit blocker/error stops the landing.",
        "",
        "#### 2a.1. Register Search Console (immediately after the landing publishes)",
        "As soon as 2a reports `surface_refresh.publish.status == \"published\"` for the landing, register the live site with Google Search Console — do this BEFORE 2b so the single fast idempotent call is front-loaded onto the already-live landing instead of being pushed past the budget by the heavier 2b pass.",
        "Call business_register_search_console with the business and a fresh idempotency_key. It injects the google-site-verification META tag onto BOTH the live published landing and the source template (so Google can verify it now AND the 2b appkit publish carries the tag forward), then registers the URL-prefix property.",
        "This is live-only, key-behind-TK, and fails closed on its own: if it returns blocked_search_console_unconfigured (the verification key is not provisioned) or any other blocker, record that exact blocker in research/strategy.md and continue to 2b — do not fabricate a verification and do not stop the whole build for it.",
        "",
        "#### 2b. Add the real logo, then finish the /app access shell + profile",
        "Once the landing page has published in 2a:",
        "",
        "First, generate the real brand logo so it lands before the next publish serves it. Load takyon-brand-logo (skill_view) and follow its procedure: assemble `business_context` ({name, category, tone}) from the research you wrote in research/strategy.md (do not invent brand voice), then call business_generate_logo with the business, a fresh idempotency_key, and that business_context. The tool publishes /brand-logo.png plus a real PNG favicon onto the live site; the 2b publish below serves them, replacing the seeded monogram placeholder. business_generate_logo is live-only and creative-credit gated and fails closed on its own: if it returns insufficient credits or an unconfigured provider key, record that exact blocker in research/strategy.md, leave the seeded monogram placeholder in place, and continue with the rest of 2b — do not fabricate a logo and do not stop the whole build for it.",
        "",
        "Then finish the access shell and account page in a SECOND business_claude_agent_task with:",
        "- workspace: product/site",
        "- instruction: Use the same pinned Vite scaffold and the same single coherent visual direction you chose for the landing page in 2a — match the landing brand exactly, do not introduce a second style. Keep the shared runtime wiring through `src/lib/takyon.ts` and `src/lib/hooks.ts`.",
        '- guidance_skills: ["claude-design", "claude-design-openai", "claude-design-stripe", "claude-design-superhuman", "claude-design-vibrant", "claude-design-doodle"] so this pass receives the same shared design method and style packs.',
        "- instruction addendum: for `/app` and `/app/profile`, keep subscription/account truth on the shared AppKit hooks in `src/lib/hooks.ts`. Treat the account rail as `user` plus `entitlements[]`, and do not hand-roll gates from legacy fields like `has_active_subscription`, nested `subscription.status`, or ad hoc `client.account()` parsing.",
        "- Scope this pass to the access shell and account page on the EXISTING seeded auth + checkout rails:",
        "  - Make `/app` a thin sign-in/subscription access gate by refining `src/screens/app-layout.tsx` and `src/screens/app-home.tsx`.",
        "  - Make `/app/profile` the truthful account/subscription page in `src/screens/profile.tsx` on the existing account + profile rails.",
        "- Do not edit `src/screens/landing.tsx` again unless a small correction is required to keep it consistent with the brand; 2a already published it.",
        "- Do not spend bootstrap time editing `src/screens/support.tsx` unless explicitly asked.",
        "- Keep the shared Vite route skeleton intact unless a small route-level correction is required for correctness.",
        "- Stop once `/`, `/app`, and `/app/profile` are truthful and publishable; do not spend first-pass time inventing the real product workflow.",
        "",
        "This must NOT look like a generic starter kit, membership template, or placeholder SaaS shell.",
        'Do not leave generic copy such as "membership pricing", "what is included", "simple pricing", "offer", or similar starter text anywhere customer-visible.',
        "Keep Hermes/Takyon runtime rails for auth, account, profile, and checkout intact.",
        "But replace generic starter copy, generic starter sections, and generic starter-shell presentation with product-specific content and UI on the first pass.",
        "Keep /app present and wired through the existing Hermes app kit runtime rails for sign-in, subscription, account, and profile access.",
        "Do NOT build a bespoke product application, custom backend workflow, domain-specific dashboard, fake coach/product tabs, sample domain data, charts, or invented in-app flows on this first pass.",
        "",
        "For /:",
        "- Write ICP-specific copy immediately.",
        "- The hero, problem, features, pricing, and CTA must reflect the researched customer and pain.",
        "- The landing page should be bold, visually opinionated, and unmistakably product-specific from the first pass, not timid, generic, or scaffold-like.",
        "",
        "For /app:",
        "- Keep the existing AppKit auth, checkout/subscription, account, and profile flows.",
        "- Make the existing sign-in, subscription, account, and profile surfaces polished, branded, and customer-specific instead of generic starter UI.",
        "- You may restyle and refine those surfaces so they match the landing page brand.",
        "- Keep access decisions on the shared `src/lib/hooks.ts` helpers; prefer `useViewerAccess()` and `resolveViewerCta()` over screen-local subscription parsing.",
        "- Treat runtime account truth as `user` plus `entitlements[]`; do not gate from legacy fields like `has_active_subscription`, nested `subscription.status`, or bespoke `client.account()` adapters in the screens.",
        "- Do not invent product-specific tabs, custom product workflows, domain objects, or unsupported backend capabilities.",
        "- Do not fake persistence, fake synced records, fake AI results, or fake customer data.",
        "",
        "Implementation bias:",
        "- Edit the seeded thin access/account surfaces in place first.",
        "- Preserve `_takyon/*`, `src/lib/takyon.ts`, `src/lib/hooks.ts`, and the existing runtime rail behavior.",
        "- Prefer upgrading the existing auth/account/profile shell over creating a new app architecture.",
        "",
        "Constraints:",
        "- Keep auth, account, profile, and checkout wired to Hermes/Takyon rails.",
        "- Do not expose runtime-internal wording to customers.",
        "- Do not invent unsupported backend capabilities.",
        "- The result should be publishable and product-specific on the first pass.",
        "- refresh_surface: true",
        "",
        "Inspect the structured result from business_claude_agent_task.",
        "Trust only its exact success/blocker and surface_refresh publish status for product completion.",
        "If the product build or publish is blocked, record that exact blocker in research/strategy.md and stop bootstrap there.",
        "Do not paraphrase a different platform diagnosis and do not continue to X as if the product build completed.",
        "",
        "Once the product site is published, register its public URL with the operator's Search Console service account so the new site is owned and trackable from day one:",
        f"- Call business_seo_add_property with site_url \"https://{slug}.{_company_base_domain()}/\".",
        "- This is internal plumbing: never mention search console, indexing, ownership, or the tool name in any customer-visible sentence, and do not give it its own milestone card.",
        "- If it is blocked (the Search Console service-account secret is not configured in Safebox, or the URL is not under an owner-verified parent property), record the exact blocker in research/strategy.md and continue the remaining steps; do not fake success and do not abort bootstrap for it.",
        "",
        "### 3. Deepen research with real evidence (now the landing is live)",
        "The landing is already published and registered, so the heavier market/X research no longer blocks the customer seeing their site. This is the FIRST web-evidence pass of the whole bootstrap — none ran before the landing. Load takyon-market-research (skill_view) and run its FULL procedure now, including the live web_search + web_extract evidence gathering it requires, to deepen research/strategy.md beyond the idea-only landing brief from step 1: expand the customer/problem evidence with sourced findings, validate the offer and pricing, and lock the X angle.",
        "Update research/strategy.md in place; this deeper pass informs the X post in step 4 and the later in-app workflow. Keep all claims truthful and evidence-backed; stop once the X claims are sound.",
        "",
        "### 4. X post",
        "Load takyon-x (skill_view) and execute its procedure to draft and publish one X post about this business.",
        "Use research findings to make the post truthful and compelling.",
        "Before any live X publish or paid creative/ad action, call business_read_channel_credit_budgets. If the required bucket cannot cover the action cost, record that exact blocker in research/strategy.md and stop before enqueueing or launching the spendful step.",
        "For broader distribution-thread execution, load takyon-distribution.",
        "",
        "## Constraints",
        "Never fake auth, sessions, users, entitlements, checkout, subscriptions, outreach sends, deploys, revenue, metrics, or provider results.",
        "If a product feature is not wired to Hermes/Takyon rails, keep the customer surface normal and unavailable.",
        "Do not invent product workflow, extra tabs, or speculative routes unless the operator explicitly asked.",
        "Missing credentials, budget authority, or provider gates are blockers; hard-fail instead of creating fake receipts.",
        "If any business_* tool says the business does not exist, stop immediately and report a platform provisioning failure.",
        "Do not retry business_write_file, and do not call business_create_workspace to paper over a missing business row.",
        "If a later non-product step is blocked, record the exact blocker and stop that step without inventing a fake success.",
        "",
        "## Final response",
        "Concise status only: business filesystem root, what was created, what is blocked or missing.",
        "If a blocker has a clear next unblocked move, name that one re-run or follow-up explicitly.",
        "When the landing page and /app access shell are up and unblocked, name the next move explicitly: continue into takyon-product-workflow to build the real /app MVP (the gated in-app workflow), since the landing + access shell is a starting point, not the finished product. Describe this in warm customer-facing business language, not internal skill or tool names.",
    ]
    return "\n".join(lines)


def _ceo_bootstrap_turn_config(
    slug: str,
    goal: str,
    active_mode: str,
    *,
    business_name: str = "",
) -> dict[str, Any]:
    return {
        "user_prompt": _business_bootstrap_instruction(
            slug,
            goal,
            active_mode,
            business_name=business_name,
        ),
        "ephemeral_system_prompt": _load_ceo_prompt(),
        # Same CEO toolset as the interactive/cron turns: ``takyon-authority`` carries the spendful
        # business methods this first-business turn legitimately drives (e.g. app-plan/access-shell
        # provisioning). They stay quarantined in their own toolset (never folded into ``takyon``) so
        # they cannot leak into generic Hermes/sub-agent/product-runtime contexts; the tools are
        # fail-closed money gates and worker-only operations self-guard against session-bound calls.
        "enabled_toolsets": ["takyon", "takyon-authority", "web", "skills"],
        "disabled_toolsets": [
            "cronjob",
            "messaging",
            "clarify",
            "memory",
            "session_search",
            "terminal",
            "file",
            "browser",
            "code_execution",
        ],
        "load_soul_identity": False,
        "skip_memory": True,
        "skip_context_files": True,
        "max_turns": _DEFAULT_BOOTSTRAP_MAX_TURNS,
    }


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
                # A worker restart should requeue a wake instead of permanently blocking it.
                max_attempts=5,
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


def _enqueue_pg_ceo_bootstrap(
    store: TakyonStore,
    slug: str,
    *,
    goal: str,
    mode: str,
    schedule: str | None,
    max_turns: int,
) -> dict[str, Any]:
    try:
        from . import jobs
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon import jobs

    payload: dict[str, Any] = {
        "goal": goal,
        "mode": mode,
        "max_turns": max(1, int(max_turns or 1)),
        "estimate_cents": _operator_turn_estimate_cents(),
    }
    if schedule:
        payload["schedule"] = schedule

    with store._connect() as conn:
        with store._leaf_conn(conn) as raw:
            for existing in jobs.list_jobs(raw, slug, limit=20):
                if existing.kind == "ceo_bootstrap" and existing.status in {"queued", "running"}:
                    return {
                        "action": "ceo_bootstrap.enqueue",
                        "business": slug,
                        "job_id": str(existing.id),
                        "status": str(existing.status),
                        "created": False,
                        "schedule": schedule or "",
                    }
            job = jobs.enqueue(
                raw,
                slug,
                "ceo_bootstrap",
                idempotency_key=_idempotency_key("operator-bootstrap", slug, uuid.uuid4().hex),
                payload=payload,
                # Bootstrap is the create-time critical path; keep the normal queue retry cushion.
                max_attempts=5,
            )
    return {
        "action": "ceo_bootstrap.enqueue",
        "business": slug,
        "job_id": str(job.id),
        "status": str(job.status),
        "created": True,
        "schedule": schedule or "",
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
        workspace_root = str(store._business_root(business).resolve())
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
            lines.append(f"checkout intent created for business:{business}")
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
            surface_refresh = data.get("surface_refresh") if isinstance(data.get("surface_refresh"), dict) else {}
            if surface_refresh:
                status = surface_refresh.get("status") or "unrefreshed"
                receipt = surface_refresh.get("receipt_path") or ""
                suffix = f" -> {_business_artifact_path(business, receipt)}" if receipt else ""
                lines.append(f"product publish check {status}{suffix}")
            agent_record = data.get("agent_record") if isinstance(data.get("agent_record"), dict) else {}
            for line in _tool_progress_lines("business_record_agent", {"business": business}, agent_record)[:1]:
                lines.append(line)
        return lines
    if not results and str(name or "") == "business_refresh_product_surface":
        business = str(data.get("business") or args.get("business") or "").strip()
        surface_refresh = data.get("surface_refresh") if isinstance(data.get("surface_refresh"), dict) else {}
        if business and surface_refresh:
            status = surface_refresh.get("status") or "unrefreshed"
            receipt = surface_refresh.get("receipt_path") or ""
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
                lines.append(f"app plan policy updated for business:{business}{suffix}")
        elif action in {"app.customer.upsert", "app.entitlement.upsert"}:
            if business:
                lines.append(f"app customer/entitlement state updated for business:{business}")
        elif action == "app.usage.record":
            if business:
                lines.append(f"app usage recorded for business:{business}")
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
    try:
        data = store.read(scope=_scope_for_business(slug), query="summary")
    except TakyonError:
        return False
    business = (data.get("business") or {}) if isinstance(data, dict) else {}
    return str(business.get("slug") or "").strip() == _slugify(slug)


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
    from takyon_cli.config import save_env_value
    from . import safebox

    if len(argv) < 2 or argv[1] in {"list", "ls"}:
        load_takyon_env()
        names = safebox.list_env_backed_keys(sensitive_only=False)
        return "Secret keys:\n" + ("\n".join(f"  {name}=<redacted>" for name in names) if names else "  none found")
    if argv[1] != "set" or len(argv) < 4:
        raise SystemExit("usage: takyon secret list | takyon secret set KEY VALUE")
    key = argv[2].strip()
    if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
        raise SystemExit("secret key must be an env-style name")
    value = " ".join(argv[3:])
    if safebox.is_sensitive_env_key(key):
        safebox.save_env_backed_value(key, value)
    else:
        save_env_value(key, value)
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
            raise SystemExit('usage: /create [--live] [--no-auto] [--schedule "every 6h"] <business> [goal]')
        command_argv = ["create", *tokens[1:]]
        slug, _raw_name, _goal, _mode, _schedule, _auto_start, _no_auto = _parse_business_start_args(
            command_argv,
            usage='usage: /create [--live] [--no-auto] [--schedule "every 6h"] <business> [goal]',
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
        if isinstance(result, dict) and result.get("bootstrap_job"):
            return (
                f"Create started for business:{slug}. Refresh status or open the business after a moment "
                "to see files, blockers, and deliverables.",
                slug,
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
    from takyon_cli.runtime_provider import resolve_runtime_provider

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
        "When the operator names the app or business in a create/build request, treat that exact requested name as canonical on the first pass; "
        "preserve it in business creation and do not invent a different umbrella brand, parent company, or alternate product name unless the operator explicitly asks for a rename or split. "
        "Do not answer with a command recipe, implementation checklist, or 'say X and I will' handoff unless the operator "
        "explicitly asks for explanation only or says not to implement. "
        "Use concrete business_* tools for all durable business state changes. "
        "If an operator asks for a video/image, outreach publication, website, deploy, checkout, provider call, or other first-class artifact, use the matching business tool or report the exact missing gate; do not substitute a Markdown brief. "
        "Do not narrate private setup with phrases like 'Good, I have the full business context' or 'Now I will'; answer, act, ask one necessary question, or report the blocker. "
        "Read business state before broad changes. Honor a business work_focus of marketing or product as an operator constraint "
        "for manual turns and scheduled wakes. Keep every write business-scoped. "
        "Do not claim a file write, budget allocation, job enqueue, agent record, wakeup schedule, auth state, billing state, "
        "checkout, subscription, entitlement, deploy, outreach, revenue, metric, or provider result succeeded unless the specific "
        "business tool returned success or a concrete receipt exists. Never fake product behavior; use Hermes rails or keep unavailable features out of customer-facing debug states."
    )

    progress = _ShellProgress(show_indicator and not show_agent_activity)
    resolved_operator_user_id = _resolved_operator_user_id(operator_user_id)
    reservation_key = ""
    reserved_cents = 0
    billing_warning = ""
    agent_box: dict[str, Any] = {}
    session_context_tokens: list[Any] = []

    def invoke() -> tuple[dict[str, Any], int]:
        from .operator_gateway import build_operator_gateway_agent

        runtime = resolve_runtime_provider(
            requested=provider or None,
            target_model=resolved_model,
        )
        agent = build_operator_gateway_agent(
            runtime=runtime,
            model=resolved_model,
            operator_user_id=resolved_operator_user_id,
            business_slug=current_business,
            agent_kwargs={
                "max_iterations": max_turns,
                # ``takyon-authority`` carries the CEO's spendful business methods (logo/ad/x-search/
                # app-plan/checkout/etc.); they are quarantined into a separate toolset so they never
                # leak into generic Hermes contexts, but the operator CEO turn is the role that owns
                # them. They are fail-closed money gates; worker-only operations self-guard against
                # session-bound calls regardless of toolset membership.
                "enabled_toolsets": ["takyon", "takyon-authority", "web", "skills", "todo"],
                "disabled_toolsets": [
                    "cronjob",
                    "messaging",
                    "memory",
                    "session_search",
                    "terminal",
                    "file",
                    "browser",
                    "code_execution",
                ],
                "ephemeral_system_prompt": ceo_prompt,
                "load_soul_identity": False,
                "skip_memory": True,
                "skip_context_files": True,
                "platform": "takyon",
                "quiet_mode": not show_agent_activity,
                "tool_progress_callback": progress.tool_progress if progress.enabled else None,
                "tool_gen_callback": progress.tool_generating if progress.enabled else None,
                "tool_complete_callback": progress.tool_completed if progress.enabled else None,
            },
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
                        business_slug=current_business or "",
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
    load_takyon_env()
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
        return (
            "The standalone product app API server is retired. "
            "Use `takyon dashboard` for app-runtime rails; product generate now routes through the hardened runtime authority."
        )

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
            return {"success": True, "business": business, "mode": "live"}
        if mode_arg in {"on", "test"}:
            raise SystemExit("test mode is disabled; all businesses run live.")
        mode = "live" if mode_arg in {"off", "live"} else ""
        if not mode:
            raise SystemExit("usage: takyon test <business> on|off|status")
        return store.commit(
            scope=_scope_for_business(slug),
            operations=[{"action": "business.mode.set", "business": slug, "mode": mode}],
            idempotency_key=_idempotency_key("operator-test-mode", slug, mode),
            reason="operator normalized business mode to live",
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
        raise SystemExit('takyon auto was folded into creation. Use: takyon create [--live] [--schedule "every 6h"] <business> <goal>')

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
            usage=f'usage: takyon {command} [--live] [--no-auto] [--schedule "every 6h"] <business> [goal text]',
            auto_default=auto_default,
        )
        if _business_exists(store, slug):
            # Creating a fresh business must never reuse an existing one, but a slug
            # collision (e.g. the same idea created twice) should NOT strand the operator.
            # Auto-pick the next free, non-reserved slug so creation succeeds under a new
            # slug instead of hard-failing. This is the single create chokepoint that both
            # the shell /create and the dashboard create RPC funnel through.
            free_slug = _resolve_free_public_business_slug(store, slug)
            if free_slug == slug or _business_exists(store, free_slug):
                raise SystemExit(
                    f"business:{slug} already exists and a free alternative slug could not be derived."
                )
            slug = free_slug
        # Authoritative operator-wallet gate (GOAL_RULES §3 gap #2, red-team proven). EVERY operator
        # create entrypoint — dashboard.create RPC, shell /create via takyon.shell.exec, --no-auto
        # detached create, and the bare CLI create/init/build — funnels through this single
        # chokepoint before the business.upsert commit writes a businesses row. It REQUIRES the
        # operator's plan-funded allowance to be STRICTLY above 3% remaining and CONSUMES 3% of the
        # period allowance on create, atomically through the billing rail. Fails CLOSED for an
        # operator under the floor regardless of --test/--no-auto (a create still needs balance
        # authority), and is idempotent on the slug so a retried create never double-charges.
        # Fail-open only for identity-less / non-Postgres dev runs. The dashboard RPC's own call is
        # redundant-but-harmless. Raises InsufficientOperatorBalance (TakyonError subclass) which the
        # dashboard maps to the 4030 balance block.
        _operator_create_balance_preflight(resolved_operator_user_id, business_slug=slug)
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
        active = store.read(scope=_scope_for_business(slug), query="summary")
        business_record = (active.get("business") or {}) if isinstance(active, dict) else {}
        if str(business_record.get("slug") or "").strip() != slug:
            raise RuntimeError(f"business creation did not persist for {slug}")
        # Free starter creative-credit seed: open the business creative-credit account and grant 3
        # FREE credits (enough for the bootstrap X post = 1 + logo = 2) so the bootstrap logo and
        # first X auto-run instead of failing closed on a 0-credit balance. Idempotent on the slug;
        # a retried create re-grants nothing. Fail-open only for non-Postgres dev runs.
        _seed_business_free_credits(slug)
        active_mode = "live"
        if auto_start:
            bootstrap_job = _enqueue_pg_ceo_bootstrap(
                store,
                slug,
                goal=goal,
                mode=active_mode,
                schedule=schedule if should_schedule else None,
                max_turns=max(1, min(int(max_turns or _DEFAULT_BOOTSTRAP_MAX_TURNS), _DEFAULT_BOOTSTRAP_MAX_TURNS)),
            )
            return {
                "success": True,
                "business": slug,
                "mode": active_mode,
                "schedule": schedule if should_schedule else "",
                "init": business_result,
                "bootstrap_job": bootstrap_job,
            }
        if not should_schedule:
            return business_result
        cron_result = store.commit(
            scope=_scope_for_business(slug),
            operations=[
                {
                    "action": "cron.ensure_ceo_wakeup",
                    "business": slug,
                    "schedule": schedule,
                    "defer_first_run": True,
                }
            ],
            idempotency_key=_idempotency_key("operator-init-wake-v3", slug, schedule),
            reason="operator initialized business CEO wake loop",
            actor="operator",
        )
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
