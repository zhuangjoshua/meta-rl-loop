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
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

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

# Re-export shim (modularization Stage 1): these helpers moved verbatim to
# turn_runtime.py so worker.py stops importing the interactive CLI module.
# Kept as re-exports until every shell/test caller is proven moved.
from .turn_runtime import (  # noqa: F401
    _BOOTSTRAP_CUSTOMER_ACTION_RE,
    _BOOTSTRAP_FEATURE_NOUN_RE,
    _BOOTSTRAP_PRODUCT_BASE_DOMAIN,
    _BOOTSTRAP_PRODUCT_SHAPE_RE,
    _BOOTSTRAP_WORKFLOW_REQUEST_RE,
    _CEO_PROMPT_PATH,
    _DEFAULT_BOOTSTRAP_MAX_TURNS,
    _WORKFLOW_BOOTSTRAP_MAX_TURNS,
    _bootstrap_goal_requests_product_workflow,
    _bootstrap_public_site_url,
    _bootstrap_turn_cap_for_goal,
    _business_artifact_path,
    _business_bootstrap_instruction,
    _business_root,
    _business_workspace_execution_context,
    _ceo_bootstrap_turn_config,
    _ceo_prompt_for_bootstrap,
    _config_bool,
    _config_path,
    _harness_root,
    _load_ceo_prompt,
    _load_harness_settings,
    _normalize_progress_text,
    _parse_tool_json_result,
    _pulse_progress_lines,
    _read_business_progress_lines,
    _read_model_config,
    _reasoning_progress_callback,
    _require_agent_model_config,
    _shell_analytics_line,
    _shell_int,
    _shell_metric_value,
    _shell_money_cents,
    _shell_progress_config,
    _strip_fenced_block,
    _takyon_reasoning_config,
    _tool_progress_lines,
)


_TAKYON_SKILL_ALIASES = {
    "market-research": "takyon-market-research",
    "build-product": "takyon-product",
    "product": "takyon-product",
    "app-runtime": "takyon-app-runtime",
    "distribution": "takyon-distribution",
    "business-pulse": "takyon-business-metrics",
    "business-metrics": "takyon-business-metrics",
}
_TAKYON_SKILL_PREFIX = "takyon-"






def _clamp_bootstrap_max_turns(goal: str, value: Any, archetype: str = "") -> int:
    cap = _bootstrap_turn_cap_for_goal(goal, archetype=archetype)
    try:
        raw = int(value or cap)
    except (TypeError, ValueError):
        raw = cap
    if cap > _DEFAULT_BOOTSTRAP_MAX_TURNS and raw <= _DEFAULT_BOOTSTRAP_MAX_TURNS:
        raw = cap
    return max(1, min(raw, cap))




_CREATE_NAME_LLM_PROMPT = (
    "Choose the canonical initial product or company name from the user's idea. "
    "If the user explicitly gives or strongly implies a name, use that exact name. "
    "Only invent a concise new name when the idea does not already imply one. "
    "Return only the name text, with no quotes, JSON, explanation, or extra words."
)


def _runtime_event_rows_for_business(
    store: TakyonStore,
    business_slug: str,
    *,
    limit: int = 300,
) -> list[dict[str, Any]]:
    slug = _slugify(str(business_slug or "").strip())
    if not slug:
        return []
    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM (
              SELECT * FROM events
              WHERE business_slug = ? AND event_type LIKE 'dashboard.run.%'
              ORDER BY created_at DESC, id DESC
              LIMIT ?
            ) recent
            ORDER BY created_at ASC, id ASC
            """,
            (slug, max(1, min(int(limit or 300), 2000))),
        ).fetchall()
    return [store._row_to_dict(row) for row in rows]


def _runtime_event_tail_entry(event: Mapping[str, Any] | dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(event, dict):
        return None
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if str(payload.get("source") or "") == "operator_shell_direct":
        return None
    stream = str(payload.get("stream") or "").strip()
    if stream == "message_delta":
        text = str(payload.get("line") or payload.get("detail") or "")
        if not text:
            return None
        return {
            "mode": "ceo_stream",
            "text": text,
            "business": str(event.get("business_slug") or "").strip(),
        }
    if stream == "message_flush":
        return {"mode": "ceo_flush"}
    status = str(payload.get("status") or event.get("event_type") or "").replace("dashboard.run.", "").strip().lower()
    if status == "heartbeat":
        return None
    command = str(payload.get("command") or "").strip()
    kind = str(payload.get("kind") or "").strip().lower()
    text = str(payload.get("line") or payload.get("detail") or "").strip()
    # Historical builds persisted raw chain-of-thought notes before the producer was fixed. Keep
    # those rows for auditability, but never replay them into an operator/customer console.
    if text.lower().startswith("reasoning ->"):
        return None
    if not (command.startswith("Claude worker ->") or kind in {"claude_agent_sdk", "task"}):
        if str(event.get("event_type") or "") == "dashboard.run.output" and text:
            return {"mode": "runtime_note", "text": text}
        return None
    if not text:
        return None
    return {
        "mode": "worker_note",
        "status": status or "output",
        "text": text,
    }


def _runtime_event_tail_label(entry: Mapping[str, Any] | dict[str, Any]) -> str:
    status = str(entry.get("status") or "").strip().lower()
    if not status or status == "output":
        return "— Agent SDK —"
    return f"— Agent SDK:{status} —"


def _deduped_worker_note_text(text: Any, *, last_text: str = "") -> str:
    note = _normalize_progress_text(text)
    if not note or note == last_text:
        return ""
    return note




def _reasoning_progress_text(name: str | None, preview: str | None) -> str:
    candidate = preview if _normalize_progress_text(preview) else name
    return _normalize_progress_text(candidate, limit=220)






def _follow_chat_matches_stream(streamed_text: str, chat_text: str) -> bool:
    streamed = _normalize_progress_text(streamed_text)
    chat = _normalize_progress_text(chat_text)
    if not streamed or not chat:
        return False
    if streamed == chat:
        return True
    shorter = min(len(streamed), len(chat))
    if shorter < 32:
        return False
    return streamed.endswith(chat) or chat.endswith(streamed)


_CLI_ONLY_COMMANDS = {
    "shell",
    "interactive",
    "business",
    "list",
    "campaign",
    "files",
    "read",
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
    "electric": "\x1b[38;2;255;215;0m",  # gold — CoScale brand (was electric blue #00B0FF)
    "amber": "\x1b[38;2;255;191;0m",
    "bronze": "\x1b[38;2;205;127;50m",
}
_THEME = {
    "brand": _ANSI["electric"],
    "primary": _ANSI["electric"],
    "secondary": _ANSI["amber"],
    "skill": _ANSI["amber"],
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
    return f"{_color('coscale', _THEME['brand'])}{_dim('/')}{scope}"


def _input_bar_top(current_business: str | None) -> str:
    width = _shell_width()
    label = f" {_input_prompt_label(current_business)} "
    fill = max(0, width - _visible_len(label))
    left = fill // 2
    right = fill - left
    return _color("─" * left, _THEME["muted"]) + label + _color("─" * right, _THEME["muted"])


def _input_prompt(current_business: str | None) -> str:
    if not sys.stdout.isatty():
        return f"coscale/{_scope_label(current_business)} > "
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
        "       ▁        ",
        " ▗▆▇▛▔ ▀▀▘  ▁   ",
        "  ▜██▆▄▃▂▁▁▂▟▇▖ ",
        "    ▀▀▀██████▛▘ ",
        "   ▃▁         ▔ ",
        "  ▟███▇▆▅▅▅▖    ",
        " ▟█████████▌    ",
        "▐██████████▊    ",
    ]


def _startup_graphic(current_business: str | None) -> str:
    width = max(92, min(_shell_width(), 116))
    # CoScale wordmark — figlet "ANSI Shadow" (generated via an online figlet tool, not hand-drawn).
    wordmark = [
        " ██████╗ ██████╗ ███████╗ ██████╗ █████╗ ██╗     ███████╗",
        "██╔════╝██╔═══██╗██╔════╝██╔════╝██╔══██╗██║     ██╔════╝",
        "██║     ██║   ██║███████╗██║     ███████║██║     █████╗  ",
        "██║     ██║   ██║╚════██║██║     ██╔══██║██║     ██╔══╝  ",
        "╚██████╗╚██████╔╝███████║╚██████╗██║  ██║███████╗███████╗",
        " ╚═════╝ ╚═════╝ ╚══════╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚══════╝",
    ]
    # Gold → amber → bronze vertical gradient (mirrors the HERMES-AGENT logo treatment).
    wordmark_tints = [
        _ANSI["electric"], _ANSI["electric"],
        _ANSI["amber"], _ANSI["amber"],
        _ANSI["bronze"], _ANSI["bronze"],
    ]
    mascot = _read_mascot_lines()
    # The mascot is now block art (chafa-rendered), colored directly in a gold→amber→bronze
    # gradient (not pixel-mapped). Pad each row to the widest so the wordmark column stays aligned.
    mascot_width = max((_visible_len(m) for m in mascot), default=16)
    mascot_tints = [
        _ANSI["electric"], _ANSI["electric"], _ANSI["electric"],
        _ANSI["amber"], _ANSI["amber"], _ANSI["amber"],
        _ANSI["bronze"], _ANSI["bronze"],
    ]

    def _mascot_cell(idx: int) -> str:
        raw = _pad_visible(mascot[idx], mascot_width) if idx < len(mascot) else " " * mascot_width
        tint = mascot_tints[idx] if idx < len(mascot_tints) else _THEME["brand"]
        return _color(raw, tint)

    rows = [_frame_line(width)]
    for index, line in enumerate(wordmark):
        tint = wordmark_tints[index] if index < len(wordmark_tints) else _THEME["brand"]
        rows.append(_framed_text(f"{_mascot_cell(index)}  {_color(line, tint)}", width))
    for index in range(len(wordmark), len(mascot)):
        rows.append(_framed_text(_mascot_cell(index), width))
    rows.extend([
        _framed_text("", width),
        _framed_text(f"{_bold('CoScale operator')} {_color('ready', _THEME['success'])}  {_dim(str(Path.cwd()))}", width),
    ])
    if current_business:
        rows.extend([
            _framed_text(f"{_dim('scope')} {_color(_scope_label(current_business), _THEME['secondary'])}    {_color('plain text', _THEME['primary'])} talks to this company CEO", width),
            _framed_text(f"{_color('/wake', _THEME['primary'])} calls the CEO    {_color('/use', _THEME['primary'])} switches company    {_color('/commands', _THEME['primary'])} lists capabilities", width),
        ])
    else:
        rows.extend([
            _framed_text(f"{_dim('scope')} {_color(_scope_label(current_business), _THEME['secondary'])}    {_color('plain text', _THEME['warning'])} disabled until /use <business>", width),
            _framed_text(f"{_color('/create', _THEME['primary'])} starts company    {_color('/use', _THEME['primary'])} enters company    {_color('/commands', _THEME['primary'])} lists capabilities", width),
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
    parser.add_argument(
        "--logs",
        "--follow-logs",
        dest="follow_logs",
        action="store_true",
        help="Tail agent.log inline while CEO-backed commands run",
    )
    parser.add_argument(
        "--raw-agent",
        action="store_true",
        help="Print raw Agent SDK tool-call args/results while CEO-backed commands run",
    )


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
        from tools.skills_sync import should_sync_legacy_skills, sync_skills

        if should_sync_legacy_skills():
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


def _format_cli_dict(value: dict) -> str:
    """Render an unhandled result dict as clean, aligned key: value lines (gemini-like) instead of
    raw JSON. Keys are dimmed; small nested structures stay compact, long ones indent. Nothing dropped."""
    if not value:
        return "(empty)"
    pad = min(26, max((len(str(k)) for k in value), default=0) + 2)
    out: list[str] = []
    for key, val in value.items():
        label = _color(str(key).ljust(pad), _THEME["muted"])
        if isinstance(val, (dict, list)):
            compact = json.dumps(val, ensure_ascii=False)
            if len(compact) <= 80:
                out.append(f"{label}{compact}")
            else:
                pretty = json.dumps(val, indent=2, ensure_ascii=False)
                out.append(f"{_color(str(key), _THEME['muted'])}:\n" + "\n".join("    " + ln for ln in pretty.splitlines()))
        elif isinstance(val, bool):
            out.append(f"{label}{_color('yes' if val else 'no', _THEME['success'] if val else _THEME['muted'])}")
        else:
            out.append(f"{label}{'' if val is None else val}")
    return "\n".join(out)


def _format_cli_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return json.dumps(value, indent=2, ensure_ascii=False)

    if "content" in value and "path" in value:
        content = str(value.get("content") or "")
        path = str(value.get("path") or "").strip()
        artifact_url = str(value.get("artifact_url") or "").strip()
        artifact_api_path = str(value.get("artifact_api_path") or "").strip()
        lines: list[str] = []
        if path:
            lines.append(f"Path: {path}")
        if artifact_url:
            lines.append(f"Artifact URL: {artifact_url}")
        elif artifact_api_path:
            lines.append(f"Artifact API: {artifact_api_path}")
        if lines:
            return "\n".join(lines + ["", content]).rstrip()
        return content

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

    if "bootstrap_job" in value and "business" in value:
        bootstrap_job = value.get("bootstrap_job") if isinstance(value.get("bootstrap_job"), dict) else {}
        if bootstrap_job:
            business_ref = value.get("business") or bootstrap_job.get("business") or "<unknown>"
            if isinstance(business_ref, dict):
                slug = business_ref.get("slug") or business_ref.get("business") or "<unknown>"
            else:
                slug = str(business_ref or "<unknown>")
            follow = value.get("follow") if isinstance(value.get("follow"), dict) else {}
            status = str(follow.get("status") or bootstrap_job.get("status") or "queued")
            follow_job_result = follow.get("result") if isinstance(follow.get("result"), dict) else {}
            live_action_verification = (
                follow_job_result.get("live_action_execution_verification")
                if isinstance(follow_job_result.get("live_action_execution_verification"), dict)
                else {}
            )
            action_execution_required = bool(
                live_action_verification.get("action_execution_required")
            )
            action_execution_status = str(
                live_action_verification.get("status") or "pending"
            ).strip().lower()
            bootstrap_completion_status = str(
                follow_job_result.get("bootstrap_completion_status") or ""
            ).strip().lower()
            took = str(follow.get("duration_display") or "").strip()
            took_suffix = f" in {took}" if took else ""
            # Durable-state truth for a failed/blocked job record: if the site is actually
            # published, the one-line verdict must say so (job-record status alone reads as a
            # total failure and misled the operator on test-2).
            published_build = str(follow.get("site_published_build") or "").strip()
            published_suffix = (
                f" — BUT the product site IS built and published (live build {published_build[:12]});"
                " only later product verification may be missing"
                if published_build and status in {"failed", "blocked"}
                else ""
            )
            # The follow-tail capped out while the job kept finalizing product state. State both
            # facts without presenting an intermediate live milestone as clean E2E completion.
            if follow.get("site_live_on_detach") and status not in {"failed", "blocked"}:
                live_suffix = f" (live build {published_build[:12]})" if published_build else ""
                return (
                    f"The product site for business:{slug} is LIVE{live_suffix}{took_suffix}, but "
                    "the bootstrap is still finalizing product state. This is not a clean "
                    "bootstrap completion. Use /use "
                    f"{slug} to inspect it, or `takyon logs -f` to watch completion."
                )
            if value.get("detached"):
                return f"Create {status} for business:{slug}. Use /use {slug} to attach."
            job_id = str(bootstrap_job.get("job_id") or "").strip()
            if status in {"completed", "blocked"} and bootstrap_completion_status == "needs_human_review":
                blocker = str(
                    follow_job_result.get("review_blocker")
                    or "a bounded bootstrap rail requires human review"
                ).strip()
                job_suffix = f" Bootstrap job: {job_id}." if job_id else ""
                return (
                    f"Create STOPPED for business:{slug}{took_suffix}: HUMAN REVIEW REQUIRED — "
                    f"{blocker}. Automation and wake scheduling are suppressed.{job_suffix}"
                )
            if status in {"completed", "blocked"} and bootstrap_completion_status == "platform_blocked":
                blocker = str(
                    follow_job_result.get("review_blocker")
                    or "the platform publish rail blocked activation"
                ).strip()
                job_suffix = f" Bootstrap job: {job_id}." if job_id else ""
                return (
                    f"Create STOPPED for business:{slug}{took_suffix}: PLATFORM PUBLISH BLOCKED — "
                    f"{blocker}. No automatic retry or human-review claim was made.{job_suffix}"
                )
            if status == "completed" and action_execution_required:
                blocker = str(live_action_verification.get("blocker") or "").strip()
                blocker_suffix = f" Blocker: {blocker}." if blocker else ""
                job_suffix = f" Bootstrap job: {job_id}." if job_id else ""
                action_label = (
                    "ACTION-VERIFIED"
                    if action_execution_status == "action_verified"
                    else "PENDING"
                )
                return (
                    f"Create build completed for business:{slug}{took_suffix}; signed-in live "
                    f"action execution verification is {action_label}.{blocker_suffix} Full browser "
                    f"workflow E2E remains REQUIRED for the requested product workflow: save and "
                    f"exact-ref reopen, plus each requested revise, copy, export, or delete step."
                    f"{job_suffix}"
                )
            if status == "completed":
                job_suffix = f" Bootstrap job: {job_id}." if job_id else ""
                return f"Create completed for business:{slug}{took_suffix}.{job_suffix}"
            if job_id:
                return f"Create {status} for business:{slug}{took_suffix}{published_suffix}. Bootstrap job: {job_id}."
            return f"Create {status} for business:{slug}{took_suffix}{published_suffix}."

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
            lines.append(f"  {slug} - {name}")
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

    return _format_cli_dict(value)


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
        summary = (
            f"deleted business:{business or item.get('business')}; "
            f"filesystem -> {filesystem.get('path')} removed={filesystem.get('removed')}; "
            f"cron removed={len(removed_cron)}; domains {domain_text}"
        )
        # Never claim a clean delete while the site is still reachable (R2 edge or legacy origin).
        if item.get("still_serving"):
            reasons = ", ".join(str(r) for r in (item.get("still_serving_reasons") or [])) or "unknown"
            summary += f"; WARNING still serving ({reasons})"
        return summary
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
        state = "enabled" if item.get("enabled") else "paused"
        return f"recurring wake schedule {state} for business:{business or item.get('business')}: {item.get('schedule') or item.get('cron_job')}"
    if action == "cron.trigger_ceo_wakeup":
        target = business or item.get("business")
        job = item.get("job") if isinstance(item.get("job"), dict) else {}
        follow = item.get("follow") if isinstance(item.get("follow"), dict) else {}
        job_id = str(job.get("job_id") or follow.get("job_id") or "").strip()
        job_status = str(follow.get("status") or job.get("status") or "").strip()
        if job_id or job_status:
            status = job_status or "queued"
            suffix = f" ({job_id})" if job_id else ""
            return f"CEO wake for business:{target} worker job {status}{suffix}"
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
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text).strip()
    inline_heading = re.split(r"\s+#{1,6}\s+", text, maxsplit=1)
    if len(inline_heading) > 1 and inline_heading[0].strip():
        text = inline_heading[0].strip()
    text = re.sub(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)", "", text).strip()
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


def _explicit_create_name_from_goal(goal: str) -> str:
    candidate = _extract_create_name_candidate(goal)
    if not candidate:
        return ""
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'/_+-]*", candidate)
    if not tokens:
        return ""
    first = tokens[0].lower()
    if first in (_CREATE_NAME_LEADING_VERBS | _CREATE_NAME_LEADING_FILLERS | _CREATE_NAME_GENERIC_NOUNS):
        return ""
    if len(tokens) > 4:
        return ""
    return candidate


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
    explicit = _explicit_create_name_from_goal(goal)
    if explicit:
        return explicit
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
        resolved_name = _derive_name_from_goal(goal)
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


_SHELL_CREATE_COMMANDS = {"create", "build", "init"}
_SHELL_CREATE_FLAGS_NO_VALUE = {"--live", "--auto", "--no-auto", "--manual", "--follow", "-f", "--detach", "--background"}
_SHELL_CREATE_FLAGS_WITH_VALUE = {"--mode", "--name", "--schedule", "--slug", "--archetype"}
_SHELL_EXPLICIT_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,79}$")


def _looks_like_create_goal_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if "\n" in text or "\r" in text:
        return True
    if text[:1] in {"-", "*", "•"}:
        return True
    if re.search(r"[.:;!?]", text):
        return True
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'/_+-]*", text)
    if len(words) >= 3:
        return True
    if words and words[0].lower() in (_CREATE_NAME_LEADING_VERBS | _CREATE_NAME_LEADING_FILLERS):
        return True
    return False


def _split_shell_command(raw: str) -> tuple[str, str]:
    clean = str(raw or "").strip()
    if not clean:
        return "", ""
    command, sep, rest = clean.partition(" ")
    return command.strip().lower(), rest.strip() if sep else ""


def _shell_create_rest_prefers_goal(rest: str) -> bool:
    text = str(rest or "").strip()
    if not text:
        return False
    first = text.split(None, 1)[0].strip()
    if not first:
        return False
    if first[:1].isupper() or first[:1] in {"-", "*", "•"}:
        return True
    lowered = first.strip(".,:;!?").lower()
    if lowered in (_CREATE_NAME_LEADING_VERBS | _CREATE_NAME_LEADING_FILLERS):
        return True
    return False


def _shell_create_argv(command: str, raw_args: str) -> list[str]:
    """Parse an interactive shell create command without shell-quoting the brief.

    The terminal shell still owns quoting for top-level `takyon create ...`. Inside
    the CoScale shell, `/create` is closer to the dashboard's text box: after any
    leading flags and optional explicit slug, the rest of the line is plain product
    brief text. That keeps natural apostrophes and unbalanced quotes from turning
    into syntax errors.
    """
    normalized_command = "create" if command in _SHELL_CREATE_COMMANDS else command
    rest = str(raw_args or "").strip()
    argv = [normalized_command]
    if not rest:
        return argv

    while rest:
        token, sep, after = rest.partition(" ")
        if token == "--":
            rest = after.strip()
            break
        if token in _SHELL_CREATE_FLAGS_NO_VALUE:
            argv.append(token)
            rest = after.strip() if sep else ""
            continue
        if token in _SHELL_CREATE_FLAGS_WITH_VALUE:
            argv.append(token)
            rest = after.strip() if sep else ""
            if not rest:
                return argv
            value, sep, after = rest.partition(" ")
            argv.append(value.strip())
            rest = after.strip() if sep else ""
            continue
        break

    if not rest:
        return argv

    if "--slug" in argv:
        argv.extend(["--", rest])
        return argv

    first, sep, after = rest.partition(" ")
    explicit_slug = bool(
        sep
        and _SHELL_EXPLICIT_SLUG_RE.fullmatch(first)
        and not _shell_create_rest_prefers_goal(rest)
    )
    if explicit_slug:
        argv.extend([first, "--", after.strip()])
    else:
        argv.extend(["--goal-only", "--", rest])
    return argv




def _intake_convert_brief_to_goal(brief_path: str) -> str:
    """Read a detailed external product brief and convert it, via the intake translator, into a
    build goal expressed in Takyon's actual capabilities. Prints the full conversion (so the
    operator SEES the doc become 'what can be built') and returns the distilled build goal.

    Fails closed: a missing file, missing operator identity, or an unavailable safebox/model raises
    SystemExit rather than creating a business with an empty or fabricated goal."""
    import os
    from pathlib import Path

    path = Path(brief_path).expanduser()
    if not path.is_file():
        raise SystemExit(f"--brief: file not found: {brief_path}")
    try:
        brief_text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SystemExit(f"--brief: could not read {brief_path}: {exc}")

    operator_user_id = str(os.environ.get("TAKYON_SESSION_USER_ID") or "").strip()
    if not operator_user_id:
        raise SystemExit(
            "--brief: no operator identity (TAKYON_SESSION_USER_ID). Run through the operator rail "
            "(scripts/takyon-operator-prod.sh) so the intake conversion has model access."
        )

    try:
        from .intake import IntakeError, convert_brief
    except ImportError:  # pragma: no cover - alternate load path
        from plugins.takyon.intake import IntakeError, convert_brief

    try:
        result = convert_brief(brief_text, operator_user_id=operator_user_id)
    except IntakeError as exc:
        raise SystemExit(f"--brief intake failed: {exc}")

    print("\n===== INTAKE: brief converted to Takyon capabilities =====")
    print(result.get("markdown", ""))
    print("===== END INTAKE (building the 'Build Goal' below) =====\n")
    goal = str(result.get("goal") or "").strip()
    if not goal:
        raise SystemExit("--brief intake produced no build goal")
    return goal


def _parse_business_start_args(
    argv: list[str],
    *,
    usage: str,
    auto_default: bool = False,
) -> tuple[str, str, str, str | None, str | None, bool, bool, bool, bool, str | None, bool]:
    tokens = list(argv[1:])
    mode: str | None = None
    schedule: str | None = None
    explicit_name: str | None = None
    slug_override: str | None = None
    archetype: str | None = None
    brief_path: str | None = None
    goal_only = False
    parse_flags = True
    auto_start = auto_default
    no_auto = False
    follow = False
    detach = False
    # Opt-in landing-hero animations. Off by default so ordinary creates keep the fast, lean 2a
    # landing pass unchanged; when set, the bootstrap 2a step gets ONE extra directive to add a
    # framer-motion hero entrance (reduced-motion gated). Does not alter the no-flag prose at all.
    animations = False
    clean: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if parse_flags and token == "--":
            parse_flags = False
        elif parse_flags and token == "--goal-only":
            goal_only = True
        elif parse_flags and token == "--test":
            raise SystemExit("test mode is disabled; remove --test. All businesses run live.")
        elif parse_flags and token == "--live":
            mode = "live"
        elif parse_flags and token == "--auto":
            auto_start = True
            no_auto = False
        elif parse_flags and token in {"--no-auto", "--manual"}:
            auto_start = False
            no_auto = True
        elif parse_flags and token in {"--follow", "-f"}:
            follow = True
            detach = False
        elif parse_flags and token in {"--detach", "--background"}:
            detach = True
            follow = False
        elif parse_flags and token in {"--animation", "--animations"}:
            animations = True
        elif parse_flags and token == "--schedule":
            index += 1
            if index >= len(tokens):
                raise SystemExit(usage)
            schedule = tokens[index]
        elif parse_flags and token == "--name":
            index += 1
            if index >= len(tokens):
                raise SystemExit(usage)
            explicit_name = str(tokens[index] or "").strip() or None
        elif parse_flags and token == "--slug":
            index += 1
            if index >= len(tokens):
                raise SystemExit(usage)
            slug_override = str(tokens[index] or "").strip() or None
        elif parse_flags and token == "--archetype":
            # The app|shopify|saas toggle (readmodular §1.2). Validate EARLY so the shell error is
            # immediate and names the available choices; the store op re-validates (defense in
            # depth — archetypes.assert_selectable is the authoritative gate either way). A known
            # but not-yet-enabled archetype fails closed with `archetype_unavailable:<key>`.
            index += 1
            if index >= len(tokens):
                raise SystemExit(usage)
            raw_archetype = str(tokens[index] or "").strip()
            if raw_archetype:
                try:
                    from plugins.takyon import archetypes as _archetypes_mod
                except ImportError:  # pragma: no cover - alternate load path
                    from . import archetypes as _archetypes_mod  # type: ignore[no-redef]
                try:
                    archetype = _archetypes_mod.assert_selectable(raw_archetype)
                except _archetypes_mod.ArchetypeError as exc:
                    raise SystemExit(str(exc))
        elif parse_flags and token == "--brief":
            index += 1
            if index >= len(tokens):
                raise SystemExit(usage)
            brief_path = str(tokens[index] or "").strip()
        elif parse_flags and token in {"-h", "--help", "help"}:
            raise SystemExit(usage)
        elif parse_flags and token.startswith("--"):
            raise SystemExit(f"unknown create flag {token!r}\n{usage}")
        else:
            clean.append(token)
        index += 1
    if not clean and not slug_override:
        raise SystemExit(usage)

    # --brief <path>: intake a detailed external product doc and CONVERT it to a build goal
    # expressed in the capabilities Takyon actually has (see plugins/takyon/intake.py). Additive:
    # absent -> unchanged behavior. The converted goal replaces any goal text; the slug still comes
    # from the positional/--slug/--name as usual, so `create <slug> --brief <path>` is the shape.
    converted_goal: str | None = None
    if brief_path:
        converted_goal = _intake_convert_brief_to_goal(brief_path)

    if goal_only or (
        auto_default
        and not slug_override
        and not explicit_name
        and len(clean) == 1
        and _looks_like_create_goal_text(clean[0])
    ):
        goal = converted_goal or " ".join(clean).strip()
        if not goal and not slug_override and not explicit_name:
            raise SystemExit(usage)
        if slug_override:
            slug = _slugify(slug_override)
            raw_name = explicit_name or _display_name_from_slug(slug)
        else:
            raw_name, slug = _resolve_create_identity(explicit_name or "", goal, "")
        return slug, raw_name, goal, mode, schedule, auto_start, no_auto, follow, detach, archetype, animations

    if slug_override:
        slug = _slugify(slug_override)
        raw_name = explicit_name or _display_name_from_slug(slug)
        goal = converted_goal or " ".join(clean).strip()
        return slug, raw_name, goal, mode, schedule, auto_start, no_auto, follow, detach, archetype, animations

    slug_token = clean[0]
    raw_name = explicit_name or slug_token
    slug = _slugify(slug_token)
    goal = converted_goal or " ".join(clean[1:]).strip()
    return slug, raw_name, goal, mode, schedule, auto_start, no_auto, follow, detach, archetype, animations


def _strip_log_follow_flags(argv: list[str], *, default: bool = False) -> tuple[list[str], bool]:
    """Remove CLI log-follow flags from arbitrary Takyon command args."""
    follow_logs = bool(default)
    clean: list[str] = []
    for token in argv:
        if token in {"--logs", "--follow-logs"}:
            follow_logs = True
        elif token == "--no-logs":
            follow_logs = False
        else:
            clean.append(token)
    return clean, follow_logs


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
    defer_settle: bool = False,
) -> dict[str, int | str] | None:
    """Create-time operator wallet chokepoint — NEUTRALIZED for the dogfooding ungate.

    This used to authoritatively gate company creation on ``allowance_percent_remaining > 3`` and
    decrement 3% of the operator plan allowance per create. That plan coupling is intentionally
    removed (see body): a Takyon user may now create any number of businesses regardless of plan
    balance. The real money chokepoint stays the per-turn runtime usage gate (``billing.reserve``),
    and the subuser/product rails are untouched. The function and its signature are retained as the
    single shell-/create- and dashboard-create funnel so the gate can be reinstated in one place."""
    from .core import _db_backend

    user_id = _resolved_operator_user_id(operator_user_id)
    if not user_id or _db_backend() != "postgres":
        return  # no billing plane to gate on (dev / identity-less) — do not block local creation

    # Operator-plane create gate REMOVED (dogfooding ungate). Company creation is intentionally
    # NOT gated on, and NOT charged against, the operator subscription plan: a Takyon user may
    # create any number of businesses regardless of plan balance, including a $0 wallet. The real
    # per-turn money chokepoint remains the runtime usage gate (billing.reserve), so severing the
    # create coupling does not open an unbounded-spend hole at the spend point. Subuser/product
    # rails (app_usage, product entitlements, product checkout, Stripe) are untouched — only the
    # operator create→plan coupling is cut, and the bootstrap starter seed is correspondingly
    # decoupled in safebox._local_grant_business_bootstrap_credits. To restore plan-gated creation,
    # reinstate the >3% allowance refusal + 3% per-create charge here (git history) and re-add the
    # settled-charge check in safebox. business_slug/defer_settle are retained for the stable
    # chokepoint signature; finalize() no-ops on the None return.
    return None


def _operator_create_balance_finalize(
    create_charge: dict[str, int | str] | None,
    *,
    settle: bool,
) -> None:
    """Settle or release a deferred company-create reservation.

    The create chokepoint reserves before writing the businesses row, so an unfunded operator still
    cannot create. It settles only once that row is durably visible; if the row write itself fails,
    the reservation is released so a transient create failure does not strand allowance.
    """
    if not create_charge:
        return
    reservation_key = str(create_charge.get("reservation_key") or "").strip()
    if not reservation_key:
        return

    try:
        from . import billing
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon import billing

    conn = _connect_operator_postgres()
    try:
        if settle:
            charge_cents = int(create_charge.get("charge_cents") or 0)
            billing.settle(conn, reservation_key, max(0, charge_cents))
        else:
            billing.release_reservation(conn, reservation_key)
    finally:
        conn.close()


def _business_bootstrap_free_credits() -> int:
    try:
        from . import safebox
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon import safebox

    return safebox.business_bootstrap_free_credits()


def _connect_operator_postgres():
    """Open a Postgres connection for operator-owned control-plane work only."""
    import psycopg

    try:
        from .runtime_app import assert_takyon_pg_role, resolve_database_url
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon.runtime_app import assert_takyon_pg_role, resolve_database_url

    conn = psycopg.connect(
        resolve_database_url(plane="operator"),
        autocommit=True,
        prepare_threshold=None,
    )
    try:
        assert_takyon_pg_role(conn, "operator")
    except Exception:
        conn.close()
        raise
    return conn


def _seed_business_free_credits(slug: str, *, operator_user_id: str | None = None) -> None:
    """Open the business creative-credit account and grant the free starter pack on create.

    Idempotent on the slug inside Safebox, so a retried create never re-grants. Fail-open only for
    identity-less / non-Postgres dev runs, where there is no authoritative create charge to verify and
    local creation must not be blocked. This funds the two required Taste site images while preserving
    the post-landing logo + first X allowance; without it those actions fail closed on a zero balance."""
    from .core import _db_backend

    business_slug = str(slug or "").strip()
    user_id = _resolved_operator_user_id(operator_user_id)
    if not business_slug or not user_id or _db_backend() != "postgres":
        return  # no creative-credit ledger to seed (dev / non-Postgres)

    try:
        from . import safebox
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon import safebox
    safebox.grant_business_bootstrap_credits(
        None,
        business_slug,
        user_id,
    )


def _try_seed_business_free_credits(
    slug: str,
    *,
    operator_user_id: str | None = None,
) -> dict[str, Any]:
    try:
        _seed_business_free_credits(slug, operator_user_id=operator_user_id)
    except Exception as exc:  # noqa: BLE001 - starter credits must not strand create before bootstrap.
        return {
            "action": "business_credits.bootstrap_free_seed",
            "business": str(slug or "").strip(),
            "status": "failed",
            "error": str(exc),
        }
    return {
        "action": "business_credits.bootstrap_free_seed",
        "business": str(slug or "").strip(),
        "status": "ok",
        "credits": _business_bootstrap_free_credits(),
    }


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

    try:
        from . import billing
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon import billing

    amount = _operator_turn_estimate_cents() if estimate_cents is None else max(0, int(estimate_cents))
    if amount <= 0:
        return ("", 0)

    conn = _connect_operator_postgres()
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

    try:
        from . import billing
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon import billing

    conn = _connect_operator_postgres()
    warning = ""
    try:
        actual = max(0, int(actual_cents or 0))
        if actual <= 0:
            billing.release_reservation(conn, reservation_key)
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






def _run_pg_ceo_wake_once(store: TakyonStore, slug: str, *, run_inline: bool = True) -> dict[str, Any]:
    try:
        from . import jobs
        from .worker_pool import WorkerPool
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon import jobs
        from plugins.takyon.worker_pool import WorkerPool

    # The shell's inline compute lane: a size-1, dispatch-less pool claiming only this
    # wake kind (modularization Stage 1 — same claim path, one constructor).
    pool = WorkerPool.inline(kinds=["ceo_wake"])
    job_key = _idempotency_key("operator-wake-now", slug, uuid.uuid4().hex)

    with store._connect() as conn:
        with store._leaf_conn(conn) as raw:
            from .claim_scope import session_claim_scope

            job = jobs.enqueue(
                raw,
                slug,
                "ceo_wake",
                idempotency_key=job_key,
                payload={"estimate_cents": _operator_turn_estimate_cents()},
                # A worker restart should requeue a wake instead of permanently blocking it.
                max_attempts=5,
                # Session ownership (Stage 2): the shell's own wake binds to its pool — the
                # inline runner below claims with the same pool identity, and the session's
                # worker pool is the fallback drainer.
                claim_scope=session_claim_scope(),
            )
            outcome = None
            record = jobs.get_job(raw, job.id)
            if run_inline:
                for _ in range(20):
                    if record is not None and record.status in {"completed", "blocked", "failed"}:
                        break
                    outcome = pool.run_one_inline(raw)
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
    # Session ownership (Stage 2): the create-time bootstrap is RESERVED for this session's
    # worker pool via the ClaimScope columns (claim_scope.py) — strict when the console opened
    # an exclusive pool, first-claim-then-spill otherwise. Replaces the payload-hint affinity.
    from .claim_scope import session_claim_scope

    bootstrap_scope = session_claim_scope()

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
                claim_scope=bootstrap_scope,
                # Bootstrap is the create-time critical path, but a full from-scratch re-run is NOT
                # idempotent across attempts (the CEO mints fresh uuid4 keys, so a retry re-tweets the
                # X launch and re-reserves the logo credit). Until the sub-step keys are derived
                # deterministically from the job id (resume-not-restart — tracked separately), bound
                # the blast radius to one retry so a transient failure can still recover without a
                # 5× re-tweet / re-charge / model-spend cascade.
                max_attempts=2,
            )
    return {
        "action": "ceo_bootstrap.enqueue",
        "business": slug,
        "job_id": str(job.id),
        "status": str(job.status),
        "created": True,
        "schedule": schedule or "",
    }


def _agent_log_path() -> Path | None:
    """Profile-safe path to the live runtime agent.log, or None when it does not exist."""
    base: Path | None = None
    try:
        from takyon_constants import get_takyon_home

        base = Path(get_takyon_home())
    except Exception:  # noqa: BLE001 - fall back to the env the launcher always exports
        home = os.environ.get("TAKYON_HOME")
        base = Path(home) if home else None
    if base is None:
        return None
    candidate = base / "logs" / "agent.log"
    return candidate if candidate.exists() else None


class _AgentLogTail:
    """Best-effort read-only tail of agent.log for CLI operator visibility."""

    _lock = threading.Lock()
    _active = 0

    _SESSION_RE = re.compile(r"\bsession=([A-Za-z0-9_.:-]+)")
    _BUSINESS_RE = re.compile(r"\bbusiness(?:_slug)?[:=]([A-Za-z0-9][A-Za-z0-9_-]{0,160})\b")

    def __init__(
        self,
        *,
        enabled: bool,
        prefix: str = "  · ",
        business_filter: str | Callable[[], str | None] | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.prefix = prefix
        self.business_filter = business_filter
        self.path: Path | None = None
        self.offset = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._out: Any = None
        self._owns_active = False
        self._session_business: dict[str, str] = {}

    def __enter__(self):
        if not self.enabled:
            return self
        if not self._claim_active_tail():
            self.enabled = False
            return self
        self.path = _agent_log_path()
        if self.path is None:
            print("[logs] agent.log not found; continuing without live log tail.", flush=True)
            self.enabled = False
            self._release_active_tail()
            return self
        try:
            self.offset = self.path.stat().st_size
            self._out = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8", errors="replace")
        except OSError:
            self.enabled = False
            self._release_active_tail()
            return self
        scope = self._current_business_filter()
        if scope:
            print(
                f"[logs] following {self.path} for business:{scope} (Ctrl-C stops the shell)",
                file=self._out,
                flush=True,
            )
        else:
            print(
                f"[logs] following {self.path} (live tails activate after /use <business>)",
                file=self._out,
                flush=True,
            )
        self._thread = threading.Thread(target=self._run, name="takyon-agent-log-tail", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        try:
            if not self.enabled:
                return
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=2.0)
            self._drain_once()
            if self._out is not None:
                try:
                    self._out.close()
                except OSError:
                    pass
        finally:
            self._release_active_tail()

    def _claim_active_tail(self) -> bool:
        with self._lock:
            if self.__class__._active:
                return False
            self.__class__._active += 1
            self._owns_active = True
            return True

    def _release_active_tail(self) -> None:
        if not self._owns_active:
            return
        with self._lock:
            self.__class__._active = max(0, self.__class__._active - 1)
        self._owns_active = False

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            self._drain_once()

    def _drain_once(self) -> None:
        path = self.path
        out = self._out
        if path is None or out is None:
            return
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
                self.offset = handle.tell()
        except OSError:
            return
        for line in chunk.splitlines():
            if self._should_print_line(line):
                print(f"{self.prefix}{line}", file=out, flush=True)

    def _current_business_filter(self) -> str:
        raw = self.business_filter
        if callable(raw):
            try:
                raw = raw()
            except Exception:
                raw = None
        return _slugify(str(raw or "").strip()) if raw else ""

    def _line_session(self, line: str) -> str:
        match = self._SESSION_RE.search(line)
        return match.group(1) if match else ""

    def _line_business(self, line: str) -> str:
        match = self._BUSINESS_RE.search(line)
        return _slugify(match.group(1)) if match else ""

    def _should_print_line(self, line: str) -> bool:
        if not line.strip():
            return False
        scope = self._current_business_filter()
        if not scope:
            return False

        session_id = self._line_session(line)
        line_business = self._line_business(line)
        if session_id and line_business:
            self._session_business[session_id] = line_business
        if line_business:
            return line_business == scope
        if session_id and session_id in self._session_business:
            return self._session_business[session_id] == scope
        return bool(re.search(rf"(?<![A-Za-z0-9_-]){re.escape(scope)}(?![A-Za-z0-9_-])", line.lower()))


class _RuntimeEventTail:
    """Best-effort read-only tail of live CEO text deltas recorded by worker jobs."""

    def __init__(
        self,
        *,
        store: TakyonStore,
        enabled: bool,
        business_filter: str | Callable[[], str | None] | None = None,
    ) -> None:
        self.store = store
        self.enabled = bool(enabled)
        self.business_filter = business_filter
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._out: Any = None
        self._seen: set[str] = set()
        self._scope = ""
        self._stream_open = False
        self._stream_business = ""
        self._last_worker_note_text = ""

    def __enter__(self):
        if not self.enabled:
            return self
        try:
            self._out = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8", errors="replace")
        except OSError:
            self.enabled = False
            return self
        self._thread = threading.Thread(target=self._run, name="takyon-runtime-event-tail", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        try:
            if not self.enabled:
                return
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=2.0)
            self._drain_once()
            self._finish_stream()
            if self._out is not None:
                try:
                    self._out.close()
                except OSError:
                    pass
        finally:
            self._out = None

    def _run(self) -> None:
        while not self._stop.wait(0.35):
            self._drain_once()

    def _current_business_filter(self) -> str:
        raw = self.business_filter
        if callable(raw):
            try:
                raw = raw()
            except Exception:
                raw = None
        return _slugify(str(raw or "").strip()) if raw else ""

    def _prime_scope(self, scope: str) -> None:
        self._finish_stream()
        self._scope = scope
        self._seen.clear()
        try:
            rows = self._runtime_event_rows(scope, limit=200)
        except Exception:
            return
        for event in rows:
            eid = str(event.get("id") or "")
            if eid:
                self._seen.add(eid)

    def _runtime_event_rows(self, scope: str, *, limit: int = 300) -> list[dict[str, Any]]:
        return _runtime_event_rows_for_business(self.store, scope, limit=limit)

    def _drain_once(self) -> None:
        if self._out is None:
            return
        scope = self._current_business_filter()
        if scope != self._scope:
            self._prime_scope(scope)
            return
        try:
            rows = self._runtime_event_rows(scope)
        except Exception:
            return
        for event in rows:
            eid = str(event.get("id") or "")
            if not eid or eid in self._seen:
                continue
            self._seen.add(eid)
            entry = _runtime_event_tail_entry(event)
            if not entry:
                continue
            mode = str(entry.get("mode") or "")
            if mode == "ceo_stream":
                self._last_worker_note_text = ""
                business = "" if scope else str(entry.get("business") or "")
                self._write_delta(str(entry.get("text") or ""), business=business)
            elif mode == "ceo_flush":
                self._last_worker_note_text = ""
                self._finish_stream()
            elif mode == "runtime_note":
                self._last_worker_note_text = ""
                self._write_runtime_note(str(entry.get("text") or ""))
            elif mode == "worker_note":
                self._write_worker_note(str(entry.get("text") or ""), status=str(entry.get("status") or "output"))

    def _write_delta(self, text: str, *, business: str = "") -> None:
        if self._out is None or not text:
            return
        if business and self._stream_business and business != self._stream_business:
            self._finish_stream()
        if not self._stream_open:
            label = f"— CEO:{business} —" if business else "— CEO —"
            print(f"\n{label}", file=self._out, flush=True)
            self._stream_open = True
            self._stream_business = business
        print(text, end="", file=self._out, flush=True)

    def _finish_stream(self) -> None:
        if self._out is None or not self._stream_open:
            self._stream_open = False
            self._stream_business = ""
            return
        print("", file=self._out, flush=True)
        self._stream_open = False
        self._stream_business = ""

    def _write_worker_note(self, text: str, *, status: str = "output") -> None:
        note = _deduped_worker_note_text(text, last_text=self._last_worker_note_text)
        if self._out is None or not note:
            return
        self._last_worker_note_text = note
        self._finish_stream()
        label = _runtime_event_tail_label({"status": status})
        print(f"\n{label}", file=self._out, flush=True)
        print(note, file=self._out, flush=True)

    def _write_runtime_note(self, text: str) -> None:
        note = _normalize_progress_text(text)
        if self._out is None or not note:
            return
        self._finish_stream()
        print(f"{_color('->', _THEME['secondary'])} {note}", file=self._out, flush=True)


def _coerce_event_datetime(value: Any) -> Any:
    """Best-effort datetime from a job/event timestamp (PG datetime or SQLite ISO text)."""
    import datetime as _dt

    if isinstance(value, _dt.datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_duration_seconds(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _bootstrap_phase_durations(store: TakyonStore, slug: str) -> list[tuple[str, float]]:
    """Per-workspace worker-phase durations (seconds) from the recorded runtime events.

    Groups historical build-process progress events (``command = 'Claude worker -> <workspace>'``)
    by workspace and takes first->last event time per group. Display-only: derived entirely
    from already-recorded events, so it works cross-machine and after reattach."""
    phases: dict[str, list[Any]] = {}
    try:
        rows = _runtime_event_rows_for_business(store, slug, limit=2000)
    except Exception:  # noqa: BLE001 - duration report is display-only
        return []
    for event in rows:
        payload = event.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:  # noqa: BLE001
                continue
        if not isinstance(payload, dict):
            continue
        command = str(payload.get("command") or "")
        if not command.startswith("Claude worker -> "):
            continue
        stamp = _coerce_event_datetime(event.get("created_at"))
        if stamp is None:
            continue
        phases.setdefault(command.removeprefix("Claude worker -> ").strip() or "worker", []).append(stamp)
    out: list[tuple[str, float]] = []
    for workspace, stamps in phases.items():
        if len(stamps) >= 2:
            out.append((workspace, (max(stamps) - min(stamps)).total_seconds()))
    out.sort(key=lambda item: -item[1])
    return out


def _follow_worker_job(
    store: TakyonStore,
    slug: str,
    job_id: str,
    *,
    label: str = "job",
    tail_logs: bool = True,
    poll_seconds: float = 2.0,
    max_seconds: float = 1800.0,
) -> dict[str, Any]:
    """Stream a worker-owned business job live to stdout.

    Tails three read-only feeds until the job is terminal: the business CEO-turn chat mirror
    (the same narration the dashboard renders), new ``agent.log`` lines (full runtime
    visibility on-box when requested), and the job status transitions. This is PURE OBSERVATION: it never
    claims, runs, or mutates the job (the worker service owns execution), so it changes no
    billing/identity authority and detaching (Ctrl-C) leaves the job running.
    """
    import time

    try:
        from . import jobs
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon import jobs

    terminal = {"completed", "blocked", "failed"}

    seen_events: set[str] = set()
    seen_runtime_events: set[str] = set()
    stream_open = False
    current_stream_text = ""
    last_streamed_ceo_message = ""
    last_worker_note_text = ""
    log_path = None
    log_offset = 0

    def _drain_new_chat() -> None:
        nonlocal last_streamed_ceo_message
        try:
            events = store.read_ceo_turn_events(slug, limit=50)
        except Exception:  # noqa: BLE001 - chat mirror is display-only
            return
        for event in reversed(events):  # read_ceo_turn_events is newest-first
            eid = str(event.get("id") or "")
            if not eid or eid in seen_events:
                continue
            seen_events.add(eid)
            payload = event.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:  # noqa: BLE001
                    payload = {}
            text = str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""
            if not text:
                continue
            if _follow_chat_matches_stream(last_streamed_ceo_message, text):
                last_streamed_ceo_message = ""
                continue
            last_streamed_ceo_message = ""
            print(f"\n— CEO —\n{text}\n", flush=True)

    def _finish_stream() -> None:
        nonlocal current_stream_text, last_streamed_ceo_message, stream_open
        if stream_open:
            print("", flush=True)
            normalized = _normalize_progress_text(current_stream_text)
            if normalized:
                last_streamed_ceo_message = normalized
        stream_open = False
        current_stream_text = ""

    def _drain_new_runtime_stream() -> None:
        nonlocal current_stream_text, last_worker_note_text, stream_open
        try:
            rows = _runtime_event_rows_for_business(store, slug, limit=300)
        except Exception:  # noqa: BLE001 - runtime stream is display-only
            return
        for event in rows:
            eid = str(event.get("id") or "")
            if not eid or eid in seen_runtime_events:
                continue
            seen_runtime_events.add(eid)
            entry = _runtime_event_tail_entry(event)
            if not entry:
                continue
            mode = str(entry.get("mode") or "")
            if mode == "ceo_stream":
                last_worker_note_text = ""
                text = str(entry.get("text") or "")
                if text:
                    if not stream_open:
                        print("\n— CEO —", flush=True)
                        stream_open = True
                    current_stream_text += text
                    print(text, end="", flush=True)
            elif mode == "ceo_flush":
                last_worker_note_text = ""
                _finish_stream()
            elif mode == "runtime_note":
                last_worker_note_text = ""
                _finish_stream()
                note = _normalize_progress_text(str(entry.get("text") or ""))
                if note:
                    print(f"{_color('->', _THEME['secondary'])} {note}", flush=True)
            elif mode == "worker_note":
                _finish_stream()
                note = _deduped_worker_note_text(
                    str(entry.get("text") or ""),
                    last_text=last_worker_note_text,
                )
                if not note:
                    continue
                last_worker_note_text = note
                print(f"\n{_runtime_event_tail_label(entry)}", flush=True)
                print(note, flush=True)

    def _drain_new_logs() -> None:
        nonlocal log_offset, log_path
        if log_path is None:
            return
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(log_offset)
                chunk = handle.read()
                log_offset = handle.tell()
        except OSError:
            log_path = None
            return
        for line in chunk.splitlines():
            if line.strip():
                print(f"  · {line}", flush=True)

    last_status = ""
    record = None
    detached = False
    detached_live_build = ""  # set when the follow-tail caps out but the site is already published
    try:
        # Prime with already-recorded turns so --follow shows only narration produced from now on.
        try:
            for event in store.read_ceo_turn_events(slug, limit=200):
                eid = str(event.get("id") or "")
                if eid:
                    seen_events.add(eid)
        except Exception:  # noqa: BLE001 - best-effort priming
            pass
        try:
            for event in _runtime_event_rows_for_business(store, slug, limit=200):
                eid = str(event.get("id") or "")
                if eid:
                    seen_runtime_events.add(eid)
        except Exception:  # noqa: BLE001 - runtime stream is display-only
            pass

        log_path = _agent_log_path() if tail_logs else None
        if log_path is not None:
            try:
                log_offset = log_path.stat().st_size  # only surface lines written after we attach
            except OSError:
                log_path = None

        print(
            f"[{label}] following job {job_id} for business:{slug} "
            "(Ctrl-C to detach; the worker job keeps running)",
            flush=True,
        )
        started = time.monotonic()
        queued_warned = False
        while True:
            _drain_new_runtime_stream()
            _drain_new_logs()
            _drain_new_chat()
            try:
                with store._connect() as conn:
                    with store._leaf_conn(conn) as raw:
                        record = jobs.get_job(raw, job_id)
            except Exception as exc:  # noqa: BLE001 - follow is best-effort observation
                print(f"[{label}] status read failed: {exc}", flush=True)
                record = None
            status = str((record.status if record else "") or "queued")
            if status != last_status:
                print(f"[{label}] {last_status or 'queued'} -> {status}", flush=True)
                last_status = status
            if status in terminal:
                break
            elapsed = time.monotonic() - started
            if status == "queued" and not queued_warned and elapsed > 30:
                print(
                    f"[{label}] still queued after 30s; confirm a worker is draining the "
                    "queue (e.g. `systemctl is-active takyon-worker.service`)",
                    flush=True,
                )
                queued_warned = True
            if elapsed > max_seconds:
                # The follow-tail hit its cap while product finalization may still be running after
                # an intermediate publication. Check the durable live-build pointer and state both truths.
                detach_live_build = ""
                try:
                    with store._connect() as conn:
                        row = conn.execute(
                            "SELECT live_build_id FROM app_surface_contracts WHERE business_slug = ?",
                            (slug,),
                        ).fetchone()
                    if row:
                        detach_live_build = str((store._row_to_dict(row) or {}).get("live_build_id") or "").strip()
                except Exception:  # noqa: BLE001 - display-only enrichment
                    detach_live_build = ""
                if detach_live_build:
                    detached_live_build = detach_live_build
                    print(
                        f"\n✓ The product site for business:{slug} is LIVE (build "
                        f"{detach_live_build[:12]}), but the bootstrap is still finalizing product "
                        "state. This is not a clean bootstrap completion. "
                        "(`takyon logs -f` to watch completion.)",
                        flush=True,
                    )
                else:
                    print(
                        f"[{label}] detaching after {int(max_seconds)}s; job still {status}. "
                        "Re-attach with `takyon logs -f`.",
                        flush=True,
                    )
                break
            time.sleep(poll_seconds)
        # Final sweep for trailing narration / log lines written just before the terminal status.
        _drain_new_runtime_stream()
        _finish_stream()
        _drain_new_logs()
        _drain_new_chat()
    except KeyboardInterrupt:
        detached = True
        print(f"\n[{label}] detached (the worker job keeps running).", flush=True)
    # Wall-clock duration from the DURABLE job row (queued -> terminal), not the local follow
    # timer — correct across detach/reattach and cross-machine claims. Phase breakdown comes
    # from the recorded worker events. Display + report only; never mutates the job.
    duration_display = ""
    if record is not None and (last_status or "") in terminal:
        started_at = _coerce_event_datetime(getattr(record, "created_at", None))
        ended_at = _coerce_event_datetime(getattr(record, "updated_at", None))
        if started_at is not None and ended_at is not None:
            duration_display = _format_duration_seconds((ended_at - started_at).total_seconds())
            phase_bits = ", ".join(
                f"{workspace} {_format_duration_seconds(seconds)}"
                for workspace, seconds in _bootstrap_phase_durations(store, slug)[:4]
            )
            print(
                f"[{label}] {last_status} in {duration_display}"
                + (f" (worker phases: {phase_bits})" if phase_bits else ""),
                flush=True,
            )
    # A terminal failed/blocked job and the business's current live pointer are two independent
    # durable facts. The pointer is business-wide and carries no job/attempt provenance, so never
    # infer that this job published it or that a later run completed this job's missing phases.
    live_build_id = ""
    if (last_status or "") in {"failed", "blocked"}:
        try:
            with store._connect() as conn:
                row = conn.execute(
                    "SELECT live_build_id FROM app_surface_contracts WHERE business_slug = ?",
                    (slug,),
                ).fetchone()
            if row:
                live_build_id = str((store._row_to_dict(row) or {}).get("live_build_id") or "").strip()
        except Exception:  # noqa: BLE001 - verdict enrichment is display-only
            live_build_id = ""
        if live_build_id:
            print(
                f"[{label}] NOTE: this job remains {last_status}. The business currently points "
                f"at live build {live_build_id[:12]}, but that business-wide pointer is not scoped "
                "to this job or attempt and does not prove this job published it. Required "
                "bootstrap steps may still be missing.",
                flush=True,
            )
    return {
        "action": f"{label}.follow",
        "job_id": str(job_id),
        "status": last_status or "queued",
        "result": (record.result if record else None),
        "error": (record.error if record else None),
        "detached": detached,
        **({"duration_display": duration_display} if duration_display else {}),
        **({"site_published_build": live_build_id or detached_live_build} if (live_build_id or detached_live_build) else {}),
        **({"site_live_on_detach": True} if detached_live_build else {}),
    }


def _follow_bootstrap_job(
    store: TakyonStore,
    slug: str,
    job_id: str,
    *,
    poll_seconds: float = 2.0,
    max_seconds: float = 1800.0,
) -> dict[str, Any]:
    return _follow_worker_job(
        store,
        slug,
        job_id,
        label="bootstrap",
        poll_seconds=poll_seconds,
        max_seconds=max_seconds,
    )


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
  takyon --logs shell        # interactive shell with inline agent.log tail
  takyon run <business> <instruction> --logs

Takyon skills through Takyon:
  {prefix} <skill-name> <instruction>
  {prefix} skill <skill-name> <instruction>
"""






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
    manifest_candidates = [
        Path(str(os.environ.get("TAKYON_CLAUDE_SKILLS_MANIFEST") or "")),
        Path(__file__).resolve().parents[2] / "skills" / "approved-skills.json",
    ]
    for manifest_path in manifest_candidates:
        if not str(manifest_path) or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        entries: list[dict[str, Any]] = []
        for item in manifest.get("skills", []):
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            entries.append(
                {
                    "command": f"/{name}",
                    "name": name,
                    "description": str(item.get("description") or "").strip(),
                    "skill_dir": str(item.get("plugin_path") or "").strip(),
                }
            )
        return sorted(entries, key=lambda entry: entry["name"])
    return []


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
        f"Takyon harness command: /{command['name']}{f' {argument_text}' if argument_text else ''}",
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

    def _row(name: str, scope: str, desc: str) -> str:
        tag = _color("biz", _THEME["secondary"]) if scope == "business" else _dim("any")
        gap = " " * max(2, 18 - len(name))
        return f"  {_color('/' + name, _THEME['primary'])}{gap}{tag}  {desc}".rstrip()

    lines = [
        _bold("CoScale shell"),
        _dim("  Plain text talks to the CEO for the current scope  ·  @path pulls a file into your message"),
        _dim("  ↑/↓ history  ·  Ctrl-R search  ·  Tab completes  ·  Shift+Enter newline"),
        "",
        _bold("Controls"),
    ]
    for command in controls or []:
        scope = "business" if command.get("requires_business") else "global"
        lines.append(_row(command["name"], scope, str(command.get("description") or "")))
    if not controls:
        lines.append(_dim("  none"))
    if commands:
        lines.append("")
        lines.append(_bold("Skill commands"))
        for command in commands:
            scope = "business" if command.get("requires_business") else "global"
            lines.append(_row(command["name"], scope, str(command.get("description") or "")))
    if skill_entries:
        lines.append("")
        lines.append(_bold("Takyon skills"))
        for item in skill_entries:
            skill_slug = str(item.get("command") or "").lstrip("/")
            lines.append(_row(skill_slug, "business", str(item.get("description") or "")))
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


_SHELL_INPUT_HISTORY: Any = None


def _shell_input_history():
    """Persistent command history for the operator shell — enables up/down recall, Ctrl-R reverse
    search, and ghost auto-suggest (the modern gemini-cli / claude-code REPL feel). Cached at module
    level so it survives the per-line PromptSession and persists across shell sessions on disk."""
    global _SHELL_INPUT_HISTORY
    if _SHELL_INPUT_HISTORY is None:
        try:
            from prompt_toolkit.history import FileHistory
            from takyon_constants import get_takyon_home

            path = Path(get_takyon_home()) / ".coscale_shell_history"
            path.parent.mkdir(parents=True, exist_ok=True)
            _SHELL_INPUT_HISTORY = FileHistory(str(path))
        except Exception:
            from prompt_toolkit.history import InMemoryHistory

            _SHELL_INPUT_HISTORY = InMemoryHistory()
    return _SHELL_INPUT_HISTORY


def _read_shell_line_prompt_toolkit(current_business: str | None, entries: list[dict[str, Any]]) -> str:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.key_binding import KeyBindings

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

    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event):  # noqa: ANN001
        # Enter accepts the highlighted completion if the menu is open; otherwise submits the line.
        buf = event.current_buffer
        if buf.complete_state and buf.complete_state.current_completion:
            buf.apply_completion(buf.complete_state.current_completion)
        else:
            buf.validate_and_handle()

    @kb.add("c-j")  # Shift+Enter on most terminals → newline, for multi-line compose
    def _newline(event):  # noqa: ANN001
        event.current_buffer.insert_text("\n")

    session = PromptSession(
        completer=SlashCompleter(),
        complete_while_typing=True,
        reserve_space_for_menu=max(4, min(_slash_page_size() + 2, 12)),
        bottom_toolbar=slash_toolbar,
        history=_shell_input_history(),
        auto_suggest=AutoSuggestFromHistory(),
        multiline=True,
        key_bindings=kb,
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
        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
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




def _raw_agent_default() -> bool:
    return _config_bool(os.getenv("TAKYON_SHELL_RAW_AGENT"), default=False)


def _raw_agent_max_chars() -> int:
    raw = str(os.getenv("TAKYON_SHELL_RAW_MAX_CHARS") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return 12000


def _shell_typewriter_config() -> dict[str, Any]:
    settings = _load_harness_settings()
    ui = settings.get("ui") if isinstance(settings.get("ui"), dict) else {}
    typewriter = ui.get("typewriter") if isinstance(ui.get("typewriter"), dict) else {}
    enabled = _config_bool(os.getenv("TAKYON_SHELL_TYPEWRITER"), default=_config_bool(typewriter.get("enabled"), default=True))
    cps_raw = os.getenv("TAKYON_SHELL_TYPEWRITER_CPS") or typewriter.get("charsPerSecond") or 1800
    try:
        cps = int(cps_raw)
    except (TypeError, ValueError):
        cps = 1800
    chunk_raw = os.getenv("TAKYON_SHELL_TYPEWRITER_CHUNK") or typewriter.get("chunkChars") or 9
    try:
        chunk_chars = int(chunk_raw)
    except (TypeError, ValueError):
        chunk_chars = 9
    return {
        "enabled": bool(enabled),
        "chars_per_second": max(200, min(cps, 12000)),
        "chunk_chars": max(1, min(chunk_chars, 80)),
    }


def _shell_json_dump(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _truncate_raw_agent(text: str, max_chars: int) -> str:
    clean = str(text or "")
    if max_chars <= 0 or len(clean) <= max_chars:
        return clean
    omitted = len(clean) - max_chars
    return f"{clean[:max_chars].rstrip()}\n... [truncated {omitted} chars; set TAKYON_SHELL_RAW_MAX_CHARS=0 for full raw output]"


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








def _credits_usage() -> str:
    return (
        "usage: takyon credits <business> [status|packs|buy <credits>|buy pack <pack-id>|"
        "reconcile <session-id>|allocate <x|meta|reddit> <credits> ...]"
    )


def _credit_bucket_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "x": "x",
        "twitter": "x",
        "meta": "meta",
        "facebook": "meta",
        "instagram": "meta",
        "fb": "meta",
        "ig": "meta",
        "reddit": "reddit",
    }
    return aliases.get(text, "")


def _credits_default_return_url(business: str) -> str:
    try:
        from .core import _dashboard_public_base_url
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon.core import _dashboard_public_base_url

    base = _dashboard_public_base_url().rstrip("/")
    if not base:
        base = "https://app.fourmanifold.com"
    return f"{base}/"


def _credit_business_owner_user_id(
    store: TakyonStore,
    business: str,
    operator_user_id: str | None,
) -> str:
    slug = _slugify(str(business or "").strip())
    if not slug:
        raise SystemExit(_credits_usage())
    store.enforce_operator_business_access(slug)
    with store._connect() as conn:
        row = conn.execute(
            "SELECT owner_user_id FROM businesses WHERE slug = ?",
            (slug,),
        ).fetchone()
    if row is None:
        raise SystemExit(f"business:{slug} does not exist")
    owner = str((row["owner_user_id"] if isinstance(row, Mapping) else row[0]) or "").strip()
    active = _resolved_operator_user_id(operator_user_id)
    if active and owner and active != owner:
        raise SystemExit(f"access denied for business:{slug}")
    user_id = active or owner
    if not user_id:
        raise SystemExit(
            "credits checkout requires a Takyon user id; set TAKYON_OPERATOR_USER_ID or run from an authenticated dashboard shell"
        )
    return user_id


def _read_credit_snapshot(business: str) -> dict[str, Any]:
    try:
        from .core import handle_business_read_channel_credit_budgets
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon.core import handle_business_read_channel_credit_budgets

    data = _parse_tool_json_result(handle_business_read_channel_credit_budgets({"business": business}))
    if not data.get("success"):
        raise SystemExit(str(data.get("error") or "failed to read creative credits"))
    value = data.get("value") if isinstance(data.get("value"), dict) else {}
    return value


def _format_credit_snapshot(business: str, snapshot: Mapping[str, Any]) -> str:
    channels = snapshot.get("channels") if isinstance(snapshot.get("channels"), Mapping) else {}
    lines = [
        f"Creative credits for business:{business}",
        (
            f"balance={_shell_int(snapshot.get('balance_credits'))} "
            f"reserved={_shell_int(snapshot.get('reserved_credits'))} "
            f"unallocated={_shell_int(snapshot.get('unallocated_credits'))}"
        ),
    ]
    for bucket in ("x", "meta", "reddit"):
        data = channels.get(bucket) if isinstance(channels.get(bucket), Mapping) else {}
        lines.append(
            f"{bucket}: allocated={_shell_int(data.get('allocated_credits'))} "
            f"used={_shell_int(data.get('used_credits'))} "
            f"reserved={_shell_int(data.get('reserved_credits'))} "
            f"remaining={_shell_int(data.get('remaining_credits'))}"
        )
    costs = snapshot.get("action_costs") if isinstance(snapshot.get("action_costs"), Mapping) else {}
    if costs:
        cost_bits = []
        for key in ("x_publish_outreach", "meta_ad_launch", "reddit_ad_launch", "static_ad_generate", "ugc_ad_generate"):
            item = costs.get(key) if isinstance(costs.get(key), Mapping) else {}
            if item:
                cost_bits.append(f"{key}={_shell_int(item.get('credits'))}")
        if cost_bits:
            lines.append("costs: " + " ".join(cost_bits))
    return "\n".join(lines)


def _current_credit_allocations(snapshot: Mapping[str, Any]) -> dict[str, int]:
    channels = snapshot.get("channels") if isinstance(snapshot.get("channels"), Mapping) else {}
    allocations: dict[str, int] = {}
    for bucket in ("x", "meta", "reddit"):
        data = channels.get(bucket) if isinstance(channels.get(bucket), Mapping) else {}
        allocations[bucket] = _shell_int(data.get("allocated_credits"))
    return allocations


def _parse_credit_allocations(args: list[str], snapshot: Mapping[str, Any]) -> dict[str, int]:
    allocations = _current_credit_allocations(snapshot)
    if not args:
        raise SystemExit("usage: takyon credits <business> allocate <x|meta|reddit> <credits> ...")
    i = 0
    changed = False
    while i < len(args):
        token = str(args[i] or "").strip()
        if not token:
            i += 1
            continue
        if token.startswith("--"):
            bucket = _credit_bucket_label(token[2:])
            if not bucket or i + 1 >= len(args):
                raise SystemExit("usage: takyon credits <business> allocate --meta 100 [--x 1 --reddit 0]")
            allocations[bucket] = _shell_int(args[i + 1])
            changed = True
            i += 2
            continue
        if "=" in token:
            key, value = token.split("=", 1)
            bucket = _credit_bucket_label(key)
            if not bucket:
                raise SystemExit(f"unknown credit bucket: {key}")
            allocations[bucket] = _shell_int(value)
            changed = True
            i += 1
            continue
        bucket = _credit_bucket_label(token)
        if not bucket or i + 1 >= len(args):
            raise SystemExit("usage: takyon credits <business> allocate meta 100 [x 1 reddit 0]")
        allocations[bucket] = _shell_int(args[i + 1])
        changed = True
        i += 2
    if not changed:
        raise SystemExit("no credit allocation was provided")
    return allocations


def _credit_checkout_options(args: list[str], business: str) -> dict[str, Any]:
    options: dict[str, Any] = {
        "credits": None,
        "pack_id": None,
        "success_url": _credits_default_return_url(business),
        "cancel_url": _credits_default_return_url(business),
    }
    i = 0
    while i < len(args):
        token = str(args[i] or "").strip()
        if token in {"--success-url", "--success"} and i + 1 < len(args):
            options["success_url"] = args[i + 1]
            i += 2
            continue
        if token in {"--cancel-url", "--cancel"} and i + 1 < len(args):
            options["cancel_url"] = args[i + 1]
            i += 2
            continue
        if token in {"--return-url", "--return"} and i + 1 < len(args):
            options["success_url"] = args[i + 1]
            options["cancel_url"] = args[i + 1]
            i += 2
            continue
        if token in {"--pack", "pack"} and i + 1 < len(args):
            options["pack_id"] = str(args[i + 1]).strip()
            i += 2
            continue
        if token.startswith("--"):
            raise SystemExit(f"unknown credits buy option: {token}")
        if options["credits"] is None:
            options["credits"] = _shell_int(token)
            i += 1
            continue
        raise SystemExit(_credits_usage())
    if not options["pack_id"] and not options["credits"]:
        raise SystemExit("usage: takyon credits <business> buy <credits>")
    return options


def _format_credit_checkout(result: Mapping[str, Any]) -> str:
    amount = _shell_money_cents(result.get("amount_cents"))
    lines = [
        f"Checkout created for business:{result.get('business_slug') or ''}",
        f"credits={_shell_int(result.get('credits'))} amount={amount}",
    ]
    if result.get("session_id"):
        lines.append(f"session_id={result.get('session_id')}")
    if result.get("checkout_url"):
        lines.append(f"checkout_url={result.get('checkout_url')}")
    if result.get("session_id"):
        lines.append(f"reconcile: /credits reconcile {result.get('session_id')}")
    return "\n".join(lines)


def _format_credit_packs() -> str:
    try:
        from . import control_api
    except ImportError:  # pragma: no cover - alternate load path as a top-level package
        from plugins.takyon import control_api

    packs = control_api.configured_creative_credit_packs()
    config = control_api.creative_credit_checkout_config()
    lines = [
        (
            f"Custom credits: {config.get('price_cents_per_credit')}c/credit, "
            f"minimum {config.get('minimum_checkout_credits')} credits"
        )
    ]
    if packs:
        lines.append("Packs:")
        for pack in packs:
            lines.append(
                f"{pack.get('id')}: {pack.get('credits')} credits for "
                f"{_shell_money_cents(pack.get('amount_cents'))}"
            )
    return "\n".join(lines)


def _handle_credits_command(
    store: TakyonStore,
    argv: list[str],
    *,
    operator_user_id: str | None = None,
) -> str:
    args = list(argv[1:])
    subcommands = {"status", "show", "packs", "buy", "checkout", "reconcile", "allocate", "alloc", "set"}
    if not args:
        raise SystemExit(_credits_usage())
    if args[0].lower() in subcommands:
        if len(args) < 2:
            raise SystemExit(_credits_usage())
        subcommand = args[0].lower()
        business = _slugify(args[1])
        rest = args[2:]
    else:
        business = _slugify(args[0])
        subcommand = args[1].lower() if len(args) >= 2 else "status"
        rest = args[2:]

    if subcommand in {"status", "show"}:
        store.enforce_operator_business_access(business)
        return _format_credit_snapshot(business, _read_credit_snapshot(business))

    if subcommand == "packs":
        store.enforce_operator_business_access(business)
        return _format_credit_packs()

    if subcommand in {"buy", "checkout"}:
        user_id = _credit_business_owner_user_id(store, business, operator_user_id)
        options = _credit_checkout_options(rest, business)
        try:
            from . import safebox
        except ImportError:  # pragma: no cover - alternate load path as a top-level package
            from plugins.takyon import safebox

        result = safebox.create_creative_credit_checkout(
            user_id,
            business,
            credits=options["credits"],
            pack_id=options["pack_id"],
            success_url=str(options["success_url"]),
            cancel_url=str(options["cancel_url"]),
        )
        return _format_credit_checkout(result)

    if subcommand == "reconcile":
        store.enforce_operator_business_access(business)
        if not rest:
            raise SystemExit("usage: takyon credits <business> reconcile <session-id>")
        try:
            from . import safebox
        except ImportError:  # pragma: no cover - alternate load path as a top-level package
            from plugins.takyon import safebox

        result = safebox.reconcile_creative_credit_checkout(
            None,
            session_id=rest[0],
            expected_business_slug=business,
        )
        credited = _shell_int(result.get("credited_credits"))
        balance = _shell_int(result.get("balance_credits"))
        return f"Credits reconciled for business:{business}: credited={credited} balance={balance}"

    if subcommand in {"allocate", "alloc", "set"}:
        store.enforce_operator_business_access(business)
        snapshot = _read_credit_snapshot(business)
        allocations = _parse_credit_allocations(rest, snapshot)
        try:
            from .core import handle_business_set_channel_credit_budgets
        except ImportError:  # pragma: no cover - alternate load path as a top-level package
            from plugins.takyon.core import handle_business_set_channel_credit_budgets

        result = _parse_tool_json_result(
            handle_business_set_channel_credit_budgets(
                {
                    "business": business,
                    "allocations": allocations,
                    "idempotency_key": _idempotency_key(
                        "operator-credit-allocation-v1",
                        business,
                        json.dumps(allocations, sort_keys=True),
                    ),
                    "reason": "operator allocated channel creative credits from CLI",
                    "actor": "operator",
                }
            )
        )
        if not result.get("success"):
            raise SystemExit(str(result.get("error") or "failed to allocate credits"))
        updated = result.get("value") if isinstance(result.get("value"), dict) else _read_credit_snapshot(business)
        return _format_credit_snapshot(business, updated)

    raise SystemExit(_credits_usage())












class _ShellProgress:
    def __init__(self, enabled: bool, *, raw_agent: bool = False):
        config = _shell_progress_config()
        typewriter = _shell_typewriter_config()
        self.enabled = bool(enabled and config["enabled"])
        self.max_lines = int(config["max_lines"])
        self.fd: int | None = os.dup(1) if self.enabled else None
        self._last_activity = ""
        self._last_tool_generating = ""
        self._reasoning_buf = ""
        self._stream_open = False
        self.streamed_chars = 0
        self.raw_agent = bool(raw_agent)
        self.raw_max_chars = _raw_agent_max_chars()
        self.typewriter_enabled = bool(typewriter["enabled"] and sys.stdout.isatty())
        self.typewriter_cps = int(typewriter["chars_per_second"])
        self.typewriter_chunk_chars = int(typewriter["chunk_chars"])
        self._spin_stop: Any = None
        self._spin_thread: Any = None

    def start_thinking(self) -> None:
        # Animated braille "thinking…" spinner shown during the wait before the first output line,
        # on the same dup'd fd. Cleared by _stop_thinking() the instant real output arrives — so it
        # never interleaves with streamed activity. Gemini-cli loading feel.
        if not self.enabled or self.fd is None or self._spin_thread is not None or not sys.stdout.isatty():
            return
        import itertools
        import threading

        cfg = _thinking_ui_config()
        if not cfg["enabled"]:
            return
        frames = cfg.get("frames") or ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        label = str(cfg.get("label") or "thinking")
        interval = float(cfg.get("interval") or 0.09)
        stop = threading.Event()
        fd = self.fd

        def _spin() -> None:
            for frame in itertools.cycle(frames):
                if stop.is_set():
                    break
                painted = f"\r{_color(frame, _THEME['primary'])} {_dim(label + '…')}\x1b[K"
                try:
                    os.write(fd, painted.encode("utf-8", errors="replace"))
                except OSError:
                    break
                stop.wait(interval)

        self._spin_stop = stop
        self._spin_thread = threading.Thread(target=_spin, daemon=True)
        self._spin_thread.start()

    def _stop_thinking(self) -> None:
        if self._spin_thread is None:
            return
        try:
            if self._spin_stop is not None:
                self._spin_stop.set()
            self._spin_thread.join(timeout=0.4)
        finally:
            self._spin_thread = None
            self._spin_stop = None
            if self.fd is not None:
                try:
                    os.write(self.fd, b"\r\x1b[2K")
                except OSError:
                    pass

    def close(self) -> None:
        self._stop_thinking()
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _write(self, text: str) -> None:
        if self.fd is None:
            return
        if self._spin_thread is not None:
            self._stop_thinking()
        try:
            os.write(self.fd, text.encode("utf-8", errors="replace"))
        except OSError:
            self.close()

    def _write_natural_text(self, text: str) -> None:
        if self.fd is None:
            return
        if not self.typewriter_enabled:
            self._write(text)
            return
        import time

        chunk = max(1, self.typewriter_chunk_chars)
        delay = chunk / max(1, self.typewriter_cps)
        for index in range(0, len(text), chunk):
            self._write(text[index : index + chunk])
            if self.fd is None:
                return
            time.sleep(delay)

    def finish_stream(self) -> None:
        if not self._stream_open:
            return
        self._write("\n")
        self._stream_open = False

    def emit(self, line: str) -> None:
        if self.fd is None:
            return
        self.finish_stream()
        self._write(f"{_color('->', _THEME['secondary'])} {line}\n")

    def raw_event(self, label: str, payload: Any) -> None:
        if self.fd is None or not self.raw_agent:
            return
        self.finish_stream()
        body = _truncate_raw_agent(_shell_json_dump(payload), self.raw_max_chars)
        self._write(f"{_color('agent.raw', _THEME['warning'])} {label}\n{body}\n")

    def agent_turn(self, text: str, *, already_streamed: bool = False) -> None:
        if self.fd is None or already_streamed:
            return
        clean = str(text or "").strip()
        if not clean:
            return
        self.finish_stream()
        self._write(f"{_color('— Takyon —', _THEME['primary'])}\n")
        self._write_natural_text(f"{clean}\n")

    def stream_delta(self, delta: Any) -> None:
        if delta is None:
            self.finish_stream()
            return
        text = str(delta or "")
        if not text:
            return
        self.streamed_chars += len(text)
        self._stream_open = True
        self._write_natural_text(text)

    def tool_generating(self, name: str) -> None:
        if not name:
            return
        if name == self._last_tool_generating:
            return
        self._last_tool_generating = name
        self.emit(f"preparing tool -> {name}")

    def activity(self, desc: str) -> None:
        text = str(desc or "").strip()
        if text == "receiving stream response":
            return
        if not text or text == self._last_activity:
            return
        self._flush_reasoning()
        self._last_activity = text
        self.emit(f"agent -> {text}")

    def _flush_reasoning(self) -> None:
        self._reasoning_buf = ""

    def tool_progress(self, event_type: str, name: str | None = None, preview: str | None = None, args: dict[str, Any] | None = None, **kwargs: Any) -> None:
        if not name:
            return
        if event_type == "tool.started":
            self._flush_reasoning()
            self._last_tool_generating = ""
            suffix = f" · {preview}" if preview else ""
            self.emit(f"tool started -> {name}{suffix}")
        elif event_type == "tool.completed":
            self._flush_reasoning()
            duration = kwargs.get("duration")
            suffix = f" · {duration:.1f}s" if isinstance(duration, (int, float)) else ""
            self.emit(f"tool completed -> {name}{suffix}")
        elif event_type in {"reasoning.available", "_thinking"}:
            # Raw model reasoning is intentionally never rendered. Curated CEO updates carry
            # customer-visible planning and progress.
            return

    def tool_started(self, tool_id: str, name: str, args: dict[str, Any]) -> None:
        self.raw_event("tool_call", {"id": tool_id, "name": name, "args": args})

    def tool_completed(self, _tool_id: str, name: str, args: dict[str, Any], result: Any) -> None:
        if self.fd is None:
            return
        self.raw_event("tool_result", {"id": _tool_id, "name": name, "args": args, "result": result})
        for line in _tool_progress_lines(name, args if isinstance(args, dict) else {}, result)[: self.max_lines]:
            self.emit(line)


def _record_shell_runtime_event(
    store: TakyonStore,
    slug: str,
    *,
    status: str,
    detail: str = "",
    line: str = "",
    command: str = "shell.turn",
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "kind": "operator_shell",
        "status": status,
        "detail": detail,
        "line": line,
        "command": command,
    }
    if extra:
        payload.update({str(key): value for key, value in extra.items() if value not in (None, [], {})})
    try:
        with store._connect() as conn:
            store._record_event(
                conn,
                scope=f"business:{slug}/runtime",
                business_slug=slug,
                event_type=f"dashboard.run.{status}",
                payload=payload,
            )
    except Exception:
        pass


class _ShellRuntimeStream:
    def __init__(
        self,
        *,
        progress: _ShellProgress,
        store: TakyonStore,
        business_slug: str | None,
        command: str = "shell.turn",
    ) -> None:
        self.progress = progress
        self.store = store
        self.business_slug = _slugify(business_slug or "") if business_slug else ""
        self.command = command
        self._stream_buffer = ""
        self._stream_last_emit = 0.0

    def _record(self, *, status: str, detail: str = "", line: str = "", extra: dict[str, Any] | None = None) -> None:
        if not self.business_slug:
            return
        _record_shell_runtime_event(
            self.store,
            self.business_slug,
            status=status,
            detail=detail,
            line=line,
            command=self.command,
            extra={"source": "operator_shell_direct", **(extra or {})},
        )

    def _flush_stream_buffer(self) -> None:
        if not self._stream_buffer:
            return
        import time

        chunk = self._stream_buffer
        self._stream_buffer = ""
        self._stream_last_emit = time.monotonic()
        self._record(
            status="output",
            detail=chunk,
            line=chunk,
            extra={"stream": "message_delta"},
        )

    def finish_stream(self) -> None:
        self._flush_stream_buffer()
        self.progress.finish_stream()
        self._record(status="output", extra={"stream": "message_flush"})

    def stream_delta(self, delta: Any) -> None:
        if delta is None:
            self.finish_stream()
            return
        text = str(delta or "")
        if not text:
            return
        self.progress.stream_delta(text)
        self._stream_buffer += text
        import time

        now = time.monotonic()
        if "\n" in self._stream_buffer or len(self._stream_buffer) >= 80 or now - self._stream_last_emit >= 0.35:
            self._flush_stream_buffer()

    def agent_turn(self, text: str, *, already_streamed: bool = False) -> None:
        if already_streamed:
            return
        clean = str(text or "").strip()
        if not clean:
            return
        self.progress.agent_turn(clean, already_streamed=False)
        self._record(
            status="output",
            detail=clean,
            line=clean,
            extra={"stream": "message_delta"},
        )
        self._record(status="output", extra={"stream": "message_flush"})


@contextlib.contextmanager
def _thinking_indicator(enabled: bool):
    if not enabled or not sys.stdout.isatty():
        yield
        return
    config = _thinking_ui_config()
    if not config["enabled"]:
        yield
        return
    import itertools
    import threading

    frames = config.get("frames") or ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    label = str(config.get("label") or "thinking")
    interval = float(config.get("interval") or 0.09)
    # Animated spinner on its own dedicated line (the agent turn runs with stdio silenced, so the
    # spinner never interleaves), redrawn in place via \r and fully cleared on completion. The input
    # row is never touched. This is the gemini-cli "loading" feel.
    writer_fd = os.dup(1)
    stop = threading.Event()

    def _spin() -> None:
        for frame in itertools.cycle(frames):
            if stop.is_set():
                break
            painted = f"\r{_color(frame, _THEME['primary'])} {_dim(label + '…')}\x1b[K"
            try:
                os.write(writer_fd, painted.encode("utf-8", errors="replace"))
            except OSError:
                break
            stop.wait(interval)

    spinner = threading.Thread(target=_spin, daemon=True)
    spinner.start()
    try:
        yield
    finally:
        stop.set()
        spinner.join(timeout=0.4)
        try:
            os.write(writer_fd, b"\r\x1b[2K")
        except OSError:
            pass
        os.close(writer_fd)


def _read_shell_line(current_business: str | None, entries: list[dict[str, Any]]) -> str:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return input(f"coscale/{current_business or 'global'} > ")
    try:
        return _read_shell_line_prompt_toolkit(current_business, entries)
    except (ImportError, ModuleNotFoundError):
        pass
    sys.stdout.write(_input_bar_top(current_business) + "\n")
    sys.stdout.flush()
    return input(_input_prompt(current_business))


def _business_exists(store: TakyonStore, slug: str) -> bool:
    slug = _slugify(slug)
    if not slug:
        return False
    enforce_access = getattr(store, "enforce_operator_business_access", None)
    if callable(enforce_access):
        try:
            enforce_access(slug)
            return True
        except TakyonError:
            return False
    try:
        data = store.read(scope=_scope_for_business(slug), query="summary")
    except TakyonError:
        return False
    business = (data.get("business") or {}) if isinstance(data, dict) else {}
    return str(business.get("slug") or "").strip() == slug


def _business_upsert_commit_persisted_slug(result: Any, slug: str) -> bool:
    slug = _slugify(slug)
    if not slug or not isinstance(result, dict):
        return False
    results = result.get("results")
    if not isinstance(results, list):
        return False
    for item in results:
        if not isinstance(item, dict):
            continue
        if str(item.get("action") or "").strip() != "business.upsert":
            continue
        item_slug = _slugify(str(item.get("business") or item.get("slug") or slug))
        if item_slug == slug:
            return True
    return False


def _require_current_business(current_business: str | None) -> str:
    if not current_business:
        raise SystemExit("Select a business first with /use <business> or create one with /create <business> <goal>.")
    return current_business


def _command_with_current_business(tokens: list[str], current_business: str | None) -> list[str]:
    if not tokens:
        return tokens
    command = tokens[0].lower().lstrip("/")
    if command in {"status", "show"} and current_business:
        return ["show", current_business, *tokens[1:]]
    if command == "pulse" and len(tokens) == 1 and current_business:
        return ["pulse", current_business]
    if command in {"files", "workspace"} and current_business:
        return ["files", current_business, *tokens[1:]]
    if command == "read" and current_business:
        return ["read", current_business, *tokens[1:]]
    if command in {"jobs", "campaigns", "capabilities", "caps"} and len(tokens) == 1 and current_business:
        return [command, current_business]
    if command in {"credits", "credit"} and current_business:
        credit_args = {"status", "show", "packs", "buy", "checkout", "reconcile", "allocate", "alloc", "set"}
        if len(tokens) == 1 or tokens[1] in credit_args:
            return ["credits", current_business, *tokens[1:]]
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


def _bare_local_command_is_unambiguous(
    tokens: list[str], current_business: str | None
) -> bool:
    """Dispatch only grammar-shaped bare commands; ordinary prose stays on the CEO chat path.

    Slash commands are always explicit and bypass this check.  Bare commands remain convenient for
    their short canonical forms, but an arbitrary sentence beginning with ``read``, ``show``,
    ``test``, ``focus``, ``delete``, etc. must never be reinterpreted as an operator command.
    """
    if not tokens:
        return False
    command = tokens[0].lower().lstrip("/")
    if len(tokens) == 1:
        return command != "read"

    def _path_arg() -> bool:
        if current_business is None or len(tokens) != 2:
            return False
        value = tokens[1]
        return (
            value not in {".", ".."}
            and not value.endswith((":", ";", ",", "?", "!"))
            and ("/" in value or "." in value or value.startswith(("./", "../", "~")))
        ) or value in {".", ".."}

    if command in {"read", "show"}:
        return _path_arg()
    if command in {"files", "workspace"}:
        return _path_arg()
    if command in {"status", "pulse", "jobs", "campaigns", "workspaces", "capabilities", "caps", "businesses", "list"}:
        return False
    if command == "test":
        return current_business is not None and len(tokens) == 2 and tokens[1].lower() in {
            "on", "off", "status", "show", "test", "live",
        }
    if command == "focus":
        return current_business is not None and len(tokens) == 2 and tokens[1].lower() in {
            "all", "any", "clear", "default", "marketing", "marketing-only", "off",
            "product", "product-only", "show", "status",
        }
    if command in {"pause", "resume", "kill"}:
        target = tokens[1].lower()
        if target == "global":
            return len(tokens) == 2
        if target == "business":
            return len(tokens) == 3
        return len(tokens) == 2 and target.startswith("business:")
    if command == "delete":
        return current_business is not None and all(
            token == "confirm" or token.startswith("--") for token in tokens[1:]
        )
    if command in {"credits", "credit"}:
        return current_business is not None and tokens[1].lower() in {
            "status", "show", "packs", "buy", "checkout", "reconcile", "allocate", "alloc", "set",
        }
    if command == "budget":
        return current_business is not None and tokens[1].lower() in {"show", "status", "set"}
    if command == "memory":
        return current_business is not None and tokens[1].lower() in {"list", "record"}
    # These commands intentionally carry a free-form CEO instruction after the command word.
    if command in {"wake", "run", "goal", "/goal"}:
        return current_business is not None
    return False


def _looks_like_slug(value: str) -> bool:
    try:
        _slugify(value)
        return True
    except Exception:
        return False


def _expand_file_refs(message: str, current_business: str | None) -> str:
    """Expand @path tokens in an operator message into the file's contents (gemini-cli @-refs).
    Resolves relative to the business workspace (when scoped) then cwd; appends fenced contents so
    the CEO sees the file without copy-paste. Missing refs are left untouched."""
    import re as _re

    refs = _re.findall(r"(?<!\S)@([^\s]+)", message)
    if not refs:
        return message
    blocks: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        candidates: list[Path] = []
        p = Path(ref).expanduser()
        if p.is_absolute():
            candidates.append(p)
        else:
            if current_business:
                try:
                    candidates.append(_business_root(current_business) / ref)
                except Exception:
                    pass
            candidates.append(Path.cwd() / ref)
        for cand in candidates:
            try:
                if cand.is_file():
                    content = cand.read_text(encoding="utf-8", errors="replace")
                    if len(content) > 20000:
                        content = content[:20000] + "\n…(truncated; file longer than 20000 chars)"
                    blocks.append(f"\n\nContents of @{ref}:\n```\n{content}\n```")
                    break
            except Exception:
                continue
    return message + "".join(blocks)


def _operator_context_message(message: str, current_business: str | None) -> str:
    message = _expand_file_refs(message, current_business)
    if current_business:
        return (
            f"Scope: business:{current_business}\n"
            "CEO role: scoped business operator.\n\n"
            f"Operator request:\n{message}\n\n"
            "First read this business state with Takyon business tools. Honor the business work_focus field "
            "if it is marketing-only or product-only. Keep all durable writes business-scoped. "
            "Requests to create, build, or make a product, app, site, feature, or workflow apply to this "
            "business and must use its product workflow. Never create or switch to another business from "
            "a scoped request. The current scope is authoritative; do not infer a business name from request "
            "wording or list businesses to choose a different target."
        )
    return (
        "Scope: global\n"
        "CEO role: account/root-scope operator. Global is not the CEO; it is the top-level Takyon scope.\n\n"
        f"Operator request:\n{message}\n\n"
        "Use global reads for businesses, credentials, policy, skills, and budgets. "
        "Natural-language requests cannot create businesses. Business creation requires an explicit /create "
        "command or the dashboard create form; otherwise select an existing business before changing business, "
        "product, or customer state."
    )


def _global_plain_text_disabled_message() -> str:
    return "Plain text is disabled in global scope. Use /commands, /create, or /use <business>."


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
        lines.append("Plain text is disabled in global scope; use /use <business> to enter a business.")
    lines.extend([
        f"Model: {resolved_model}",
        f"Provider: {provider}",
    ])
    return "\n".join(lines)




def _secrets_path(store: TakyonStore) -> Path:
    return store.root.parent / "secrets" / ".env"








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
    claude_agent_model = data.get("claude_agent_model") or "(not set)"
    response_style = data.get("response_style") or "(not set)"
    show_agent_activity = data.get("show_agent_activity") or "(not set)"
    provider = data.get("provider") or "(not set)"
    return (
        f"Model provider: {provider}\n"
        f"Conversational model: {model}\n"
        f"Coding worker model: {claude_agent_model}\n"
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
    if str(os.getenv("TAKYON_SESSION_USER_ID") or "").strip():
        return
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


def _interactive_shell(
    *,
    initial_business: str | None,
    model: str,
    max_turns: int,
    follow_logs: bool = False,
    raw_agent: bool = False,
) -> None:
    store = TakyonStore()
    _seed_platform_owner_at_startup(store)
    current_business = _slugify(initial_business) if initial_business else None
    if current_business and not _business_exists(store, current_business):
        print(f"[takyon] business:{current_business} is not initialized yet. /create {current_business} <goal> will create it.")

    entries = _slash_entries()
    print(_startup_graphic(current_business))
    shell_history: list[dict[str, str]] = []
    sdk_session_id = f"cli-shell:{uuid.uuid4()}"
    raw_agent_enabled = bool(raw_agent or _raw_agent_default())

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
            raw_tokens = shlex.split(line.lstrip("/")) if line.lstrip("/") else []
        except ValueError:
            raw_tokens = []
        if raw_tokens and raw_tokens[0].lower() == "raw":
            mode = raw_tokens[1].lower() if len(raw_tokens) >= 2 else "status"
            if mode in {"on", "true", "1", "yes"}:
                raw_agent_enabled = True
            elif mode in {"off", "false", "0", "no"}:
                raw_agent_enabled = False
            elif mode in {"status", ""}:
                pass
            else:
                print("usage: /raw [on|off|status]")
                continue
            state = "on" if raw_agent_enabled else "off"
            limit = _raw_agent_max_chars()
            limit_text = "full" if limit <= 0 else f"{limit} chars/event"
            print(f"Raw Agent SDK: {state} ({limit_text})")
            continue
        try:
            output, current_business = _handle_shell_line(
                line,
                current_business=current_business,
                store=store,
                model=model,
                max_turns=max_turns,
                shell_history=shell_history,
                sdk_session_id=sdk_session_id,
                follow_logs=follow_logs,
                raw_agent=raw_agent_enabled,
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
    sdk_session_id: str | None = None,
    operator_user_id: str | None = None,
    follow_logs: bool = False,
    raw_agent: bool = False,
) -> tuple[str, str | None]:
    is_slash = line.startswith("/")
    raw = line.lstrip("/") if is_slash else line
    if is_slash and not raw.strip():
        return _render_slash_palette(_slash_entries(), "/", current_business), current_business
    command, raw_args = _split_shell_command(raw)
    if not command:
        return "", current_business
    local_answer = _local_shell_help_answer(raw, current_business=current_business)
    if local_answer:
        return local_answer, current_business

    if is_slash and command in _SHELL_CREATE_COMMANDS:
        if current_business:
            raise SystemExit(
                f"cannot create a business from business:{current_business}; use /use global first, "
                "then run an explicit /create command"
            )
        command_argv = _shell_create_argv(command, raw_args)
        if len(command_argv) < 2:
            raise SystemExit('usage: /create [--live] [--no-auto] [--follow|--detach] [--slug <slug>] [--archetype app|shopify|saas] [--schedule "every 6h"] <business-or-brief> [goal]')
        requested_slug, _raw_name, _goal, _mode, _schedule, _auto_start, _no_auto, _follow, detach, _archetype, _animations = _parse_business_start_args(
            command_argv,
            usage='usage: /create [--live] [--no-auto] [--follow|--detach] [--slug <slug>] [--archetype app|shopify|saas] [--animation] [--schedule "every 6h"] <business-or-brief> [goal]',
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
            follow_logs=follow_logs,
            raw_agent=raw_agent,
        )
        actual_slug = requested_slug
        if isinstance(result, dict):
            bootstrap_job = result.get("bootstrap_job") if isinstance(result.get("bootstrap_job"), dict) else {}
            actual_slug = (
                str(result.get("business") or bootstrap_job.get("business") or requested_slug).strip()
                or requested_slug
            )
        if isinstance(result, dict) and result.get("bootstrap_job"):
            follow_result = result.get("follow") if isinstance(result.get("follow"), dict) else {}
            bootstrap_job = result.get("bootstrap_job") if isinstance(result.get("bootstrap_job"), dict) else {}
            status = str(follow_result.get("status") or bootstrap_job.get("status") or "queued")
            if detach or bool(result.get("detached")):
                return f"Create {status} for business:{actual_slug}. Use /use {actual_slug} to attach.", current_business
            return _format_cli_value(result), actual_slug
        return _format_cli_value(result), actual_slug

    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        if is_slash:
            raise SystemExit(
                f"{exc}. For /create, paste the brief directly after the slug or use /create --slug <slug> <brief>."
            ) from exc
        if not current_business:
            return _global_plain_text_disabled_message(), current_business
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
            sdk_session_id=sdk_session_id,
            follow_logs=follow_logs,
            raw_agent=raw_agent,
        ), current_business
    if not tokens:
        return "", current_business
    command = tokens[0].lower()
    bare_create_language = not is_slash and command in _SHELL_CREATE_COMMANDS

    if is_slash and command == "ceo":
        return _format_ceo_focus(current_business, store, model), current_business

    bare_use_is_explicit = (
        command == "use"
        and not is_slash
        and len(tokens) == 2
        and (
            tokens[1].lower() in {"global", "root", "coscale", "operator"}
            or _business_exists(store, tokens[1])
        )
    )
    if command == "use" and (is_slash or len(tokens) == 1 or bare_use_is_explicit):
        if len(tokens) < 2:
            return "Using global scope", None
        slug = _slugify(tokens[1])
        if slug in {"global", "root", "coscale", "operator"}:
            return "Using global scope", None
        if not _business_exists(store, slug):
            raise SystemExit(f"business:{slug} does not exist yet. Use /create {slug} <goal>.")
        return f"Using business:{slug}", slug

    if command in {"help", "commands", "skills", "harness"} and (is_slash or len(tokens) == 1):
        return _format_harness_commands(), current_business

    harness_command = _get_harness_command(command) if is_slash and not bare_create_language else None
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
            sdk_session_id=sdk_session_id,
            follow_logs=follow_logs,
            raw_agent=raw_agent,
        ), current_business

    if (
        command in _local_command_names()
        and command != "ceo"
        and not bare_create_language
        and (is_slash or _bare_local_command_is_unambiguous(tokens, current_business))
    ):
        normalized = _command_with_current_business(tokens, current_business)
        result = run_takyon_command(
            normalized,
            model=model,
            max_turns=max_turns,
            show_activity=False,
            show_indicator=True,
            shell_history=shell_history,
            operator_user_id=operator_user_id,
            follow_logs=follow_logs,
            raw_agent=raw_agent,
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
            message = (
                f"Invoke the approved native skill `{skill_ref[1]}` for this turn. "
                f"Operator instruction: {instruction}"
            )
            return _run_agent(
                _operator_context_message(message, current_business),
                model=model or os.getenv("TAKYON_MODEL", ""),
                max_turns=max_turns,
                show_activity=False,
                show_indicator=True,
                shell_history=shell_history,
                operator_user_id=operator_user_id,
                current_business=current_business,
                sdk_session_id=sdk_session_id,
                follow_logs=follow_logs,
                raw_agent=raw_agent,
            ), current_business
        return f"Unknown slash command: /{command}. Use /commands.", current_business

    if not current_business:
        return _global_plain_text_disabled_message(), current_business

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
        sdk_session_id=sdk_session_id,
        follow_logs=follow_logs,
        raw_agent=raw_agent,
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
    manifest_candidates = [
        Path(str(os.environ.get("TAKYON_CLAUDE_SKILLS_MANIFEST") or "")),
        Path(__file__).resolve().parents[2] / "skills" / "approved-skills.json",
    ]
    alias = _TAKYON_SKILL_ALIASES.get(clean, clean)
    for manifest_path in manifest_candidates:
        if not str(manifest_path) or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            plugin_name = str((manifest.get("plugin") or {}).get("name") or "").strip()
            for item in manifest.get("skills", []):
                if not isinstance(item, Mapping):
                    continue
                approved_name = str(item.get("name") or "").strip()
                source_slug = Path(str(item.get("source_path") or "")).name
                identifiers = {
                    approved_name,
                    source_slug,
                    *(str(value or "").strip() for value in item.get("legacy_names", [])),
                }
                if clean in identifiers or alias in identifiers:
                    qualified = f"{plugin_name}:{approved_name}" if plugin_name else approved_name
                    return ("native", qualified)
        except Exception:
            continue
    return None


def _queue_skill_invocation(ctx: Any, skill_ref: str, instruction: str) -> str:
    resolved = _resolve_skill_reference(skill_ref)
    if not resolved:
        return f"Takyon could not load skill {skill_ref}."
    native_name = resolved[1]
    msg = (
        f"Invoke the approved native skill `{native_name}` for this turn. "
        f"Operator instruction: {instruction}"
    )
    if ctx is not None and hasattr(ctx, "inject_message") and ctx.inject_message(msg):
        return f"Queued Takyon skill {native_name}."
    return (
        f"Takyon loaded {skill_ref}, but no active CLI conversation was available "
        "to receive it. Use the skill slash command directly in a running session."
    )


def _queue_ceo_invocation(ctx: Any, message: str) -> str:
    prompt = (
        "Takyon operator command:\n\n"
        f"{_operator_context_message(message, None)}\n\n"
        "Use the Takyon CEO policy, approved native skills, and concrete business_* tools. Keep business state isolated."
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
    sdk_session_id: str | None = None,
    follow_logs: bool = False,
    raw_agent: bool = False,
) -> tuple[str, dict[str, Any]]:
    load_takyon_env()
    from .claude_sdk_runtime import (
        primary_sdk_session_project_key,
        run_primary_sdk_subprocess,
        stable_sdk_session_id,
    )
    from .claude_sdk_sessions import PostgresClaudeSdkSessionStore
    from .operator_gateway import (
        compose_primary_agent_system_prompt,
        primary_interactive_budget_usd,
        primary_interactive_epoch,
    )

    ceo_prompt = _load_ceo_prompt()
    store = TakyonStore()
    model_config = _read_model_config(store)
    response_style = model_config.get("response_style", "").strip().lower()
    configured_activity = _config_bool(model_config.get("show_agent_activity"), default=False)
    show_agent_activity = configured_activity if show_activity is None else bool(show_activity)
    prompt = (
        "Takyon operator command:\n\n"
        f"{message}\n\n"
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
        "business tool returned success or a concrete receipt exists. Never fake product behavior; use Takyon rails or keep unavailable features out of customer-facing debug states."
    )

    progress = _ShellProgress(show_indicator and not show_agent_activity, raw_agent=raw_agent)
    stream = _ShellRuntimeStream(
        progress=progress,
        store=store,
        business_slug=current_business,
    )
    resolved_operator_user_id = _resolved_operator_user_id(operator_user_id)
    if not resolved_operator_user_id:
        raise RuntimeError("primary Claude Agent SDK turns require an operator user")
    business_slug = str(current_business or "").strip()
    if not business_slug:
        raise RuntimeError(
            "primary Claude Agent SDK model chat requires a selected business; "
            "global slash and create commands remain local"
        )
    stable_session = stable_sdk_session_id(
        sdk_session_id
        or os.getenv("TAKYON_SESSION_KEY")
        or f"cli:{resolved_operator_user_id}:{business_slug}:{uuid.uuid4().hex}"
    )
    session_store = PostgresClaudeSdkSessionStore(
        operator_user_id=resolved_operator_user_id,
        business_slug=business_slug,
    )
    session_key = {
        "projectKey": primary_sdk_session_project_key(
            operator_user_id=resolved_operator_user_id,
            business=business_slug,
        ),
        "sessionId": stable_session,
    }
    resume_session = session_store.load(session_key) is not None
    invocation_epoch = primary_interactive_epoch()
    session_context_tokens: list[Any] = []
    tool_started_at: dict[str, float] = {}

    def sdk_progress(event: Mapping[str, Any]) -> None:
        kind = str(event.get("kind") or "runtime").strip()
        status = str(event.get("status") or "running").strip()
        raw_detail = str(event.get("detail") or "")
        if kind == "assistant":
            if progress.enabled and status == "delta":
                # SDK partial messages are the exact provider text deltas.  Do
                # not strip or reconstruct them: leading/trailing whitespace is
                # part of the customer-visible assistant response.
                stream.stream_delta(raw_detail)
            elif progress.enabled and status == "output" and progress.streamed_chars:
                # The SDK also projects the completed assistant message after
                # its deltas.  Treat it only as the message boundary so the
                # completed text is never printed a second time.
                stream.finish_stream()
            return
        detail = raw_detail.strip()
        if kind == "tool":
            return
        if kind == "skill":
            trace = event.get("trace") if isinstance(event.get("trace"), Mapping) else {}
            skill_name = str(trace.get("skill_name") or "Skill").strip()
            if progress.enabled:
                progress.emit(detail or f"skill -> {skill_name} {status}")
            return
        if detail and progress.enabled:
            progress.activity(f"{kind} -> {detail}")

    def tool_start(tool_id: str, name: str, args: Mapping[str, Any]) -> None:
        tool_started_at[tool_id] = time.monotonic()
        if progress.enabled:
            progress.tool_progress(
                "tool.started",
                name=name,
                preview="",
                args=dict(args),
            )
            progress.tool_started(tool_id, name, dict(args))

    def tool_complete(
        tool_id: str,
        name: str,
        args: Mapping[str, Any],
        result: str,
    ) -> None:
        started = tool_started_at.pop(tool_id, None)
        if progress.enabled:
            progress.tool_progress(
                "tool.completed",
                name=name,
                args=dict(args),
                duration=(time.monotonic() - started) if started else None,
            )
            progress.tool_completed(tool_id, name, dict(args), result)

    def invoke(workspace_home: object) -> tuple[dict[str, Any], int]:
        reasoning = _takyon_reasoning_config()
        effort = "high"
        if isinstance(reasoning, Mapping) and str(reasoning.get("effort") or "") in {
            "low",
            "medium",
            "high",
        }:
            effort = str(reasoning["effort"])
        result = run_primary_sdk_subprocess(
            business=business_slug,
            operator_user_id=resolved_operator_user_id,
            system_prompt=compose_primary_agent_system_prompt(ceo_prompt),
            user_prompt=prompt,
            enabled_toolsets=["takyon", "takyon-authority", "web", "skills", "todo"],
            disabled_toolsets=[
                "cronjob",
                "messaging",
                "memory",
                "session_search",
                "terminal",
                "file",
                "code_execution",
            ],
            workspace_root=str(workspace_home or ""),
            session_id=stable_session,
            resume_session=resume_session,
            session_store=session_store,
            task_id=str(sdk_session_id or stable_session),
            mode="interactive",
            epoch=invocation_epoch,
            max_turns=max_turns,
            max_budget_usd=primary_interactive_budget_usd(),
            effort=effort,
            progress_callback=sdk_progress,
            on_tool_start=tool_start,
            on_tool_complete=tool_complete,
        )
        stream.finish_stream()
        raw_cost = result.get("actual_cost_cents")
        actual_cents = max(0, int(raw_cost or 0))
        return result, actual_cents

    try:
        workspace_context = _business_workspace_execution_context(
            business_slug,
            operator_user_id=resolved_operator_user_id,
        )
        with workspace_context as workspace_home:
            if workspace_home is None:
                raise RuntimeError("primary Claude Agent SDK business workspace is unavailable")
            try:
                from gateway.session_context import set_session_vars

                session_context_tokens = set_session_vars(
                    session_key=str(sdk_session_id or stable_session),
                    user_id=resolved_operator_user_id,
                    workspace_root=str(workspace_home),
                    business_slug=business_slug,
                )
            except Exception:
                session_context_tokens = []
            with _AgentLogTail(enabled=follow_logs):
                if show_agent_activity:
                    result, actual_cents = invoke(workspace_home)
                else:
                    progress.start_thinking()
                    with _silence_process_stdio():
                        result, actual_cents = invoke(workspace_home)
        progress._stop_thinking()  # guarantee the spinner is cleared before the response is returned/printed
        final_response = str(result.get("summary") or "").strip()
        if not final_response:
            raise RuntimeError("primary Claude Agent SDK returned no final response")
        if progress.streamed_chars:
            # The exact assistant response was already emitted from SDK text
            # deltas; returning it would make the shell/one-shot printer repeat
            # the final message.
            final_response = ""
        return final_response, {
            "actual_cost_cents": actual_cents,
            "reserved_cents": 0,
            "billing_warning": "",
            "billing_mode": "provider_broker",
            "invocation_epoch": invocation_epoch,
            "session_id": stable_session,
            "model": str(result.get("model") or "deepseek-v4-pro"),
            "skill_receipt": result.get("skill_receipt"),
        }
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
    sdk_session_id: str | None = None,
    follow_logs: bool = False,
    raw_agent: bool = False,
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
        sdk_session_id=sdk_session_id,
        follow_logs=follow_logs,
        raw_agent=raw_agent,
    )
    return response








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
    follow_logs: bool = False,
    raw_agent: bool = False,
) -> Any:
    load_takyon_env()
    from .core import operator_identity_mode

    argv, follow_logs = _strip_log_follow_flags(list(argv), default=follow_logs)
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
            follow_logs=follow_logs,
            raw_agent=raw_agent,
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
        return "Connector setup is handled by provider-specific skills/tools. Use `takyon secret set KEY VALUE` for credentials and keep business state in Takyon."

    if command == "env":
        # Thin delegation to the canonical Stage-3b handler (takyon_cli.env.cmd_env) so the
        # operator entrypoint and takyon-cli expose ONE env affordance. cmd_env prints its own
        # receipt lines and raises SystemExit(1) on blocked/errored steps (the fail-closed signal).
        from types import SimpleNamespace

        from takyon_cli.env import cmd_env

        _env_rest = argv[3:]

        def _env_flag_val(flag: str) -> str:
            for i, a in enumerate(_env_rest):
                if a == flag and i + 1 < len(_env_rest):
                    return _env_rest[i + 1]
                if a.startswith(flag + "="):
                    return a.split("=", 1)[1]
            return ""

        cmd_env(SimpleNamespace(
            env_action=(argv[1].lower() if len(argv) >= 2 else None),
            env_name=(argv[2] if len(argv) >= 3 else ""),
            # first positional after the name (a node for revoke-node/restart) — not a flag,
            # and not the value consumed by --rev.
            node_name=next(
                (a for j, a in enumerate(_env_rest)
                 if not a.startswith("-") and (j == 0 or _env_rest[j - 1] != "--rev")),
                "",
            ),
            force="--force" in _env_rest,
            rev=_env_flag_val("--rev"),
            confirm="--confirm" in _env_rest,
        ))
        return None

    if command == "migrate":
        # Same single-affordance rule for the tracked migration rail (takyon_cli.migrate.cmd_migrate).
        from types import SimpleNamespace

        from takyon_cli.migrate import cmd_migrate

        cmd_migrate(SimpleNamespace(dry_run="--dry-run" in argv[1:], migrate_type=None))
        return None

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
        delete_key_parts = [
            "operator-business-delete-v1",
            slug,
            parsed_delete["confirm"],
            parsed_delete["delete_files"],
            parsed_delete["delete_cron"],
            parsed_delete["delete_domains"],
            ",".join(parsed_delete["subdomains"]),
        ]
        if parsed_delete["confirm"]:
            delete_key_parts.append(uuid.uuid4().hex)
        return store.commit(
            scope=_scope_for_business(slug),
            operations=[{"action": "business.delete", **parsed_delete}],
            idempotency_key=_idempotency_key(*delete_key_parts),
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
        raise SystemExit("takyon registry was removed. Approved skills are published once in the immutable Agent SDK plugin.")

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

    if command == "rl":
        # RL observability — read projections over the events store (source of truth, no fabrication)
        sub = (argv[1].strip().lower() if len(argv) >= 2 else "status")
        if sub in {"lessons", "lesson"}:
            rest = argv[2:]
            action = (rest[0].strip().lower() if rest else "")
            if action in {"approve", "reject"}:
                if len(rest) < 2:
                    raise SystemExit(f"usage: takyon rl lessons {action} <lesson-id> [reason]")
                return store.rl_review_lesson(rest[1], action, reason=" ".join(rest[2:]))
            slug = None
            scope = None
            status = None
            i = 0
            while i < len(rest):
                tok = rest[i]
                if tok == "--scope" and i + 1 < len(rest):
                    scope = rest[i + 1].lower(); i += 2; continue
                if tok == "--status" and i + 1 < len(rest):
                    status = rest[i + 1].lower(); i += 2; continue
                if tok in {"pending", "review"}:
                    status = "candidate"; i += 1; continue
                if not tok.startswith("--"):
                    slug = _slugify(tok)
                i += 1
            return store.rl_lessons(slug, scope=scope, status=status)
        if sub == "why":
            if len(argv) < 3:
                raise SystemExit("usage: takyon rl why <episode-id>")
            return store.rl_why(argv[2])
        if sub == "policy":
            if len(argv) < 3:
                raise SystemExit("usage: takyon rl policy <business>")
            return store.rl_policy(_slugify(argv[2]))
        if sub in {"status", ""}:
            return store.rl_status(_slugify(argv[2]) if len(argv) >= 3 else None)
        raise SystemExit("usage: takyon rl status|lessons|why|policy ...")

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
            "message": "Takyon capabilities come from approved native skills plus business_* tool gates. Skill-specific API readiness is declared in skill frontmatter.",
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

    if command in {"credits", "credit"}:
        return _handle_credits_command(
            store,
            argv,
            operator_user_id=resolved_operator_user_id,
        )

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
            wake_result = _run_pg_ceo_wake_once(store, slug, run_inline=not follow_logs)
            trigger_result["triggered"] = wake_result.get("status") in {"completed", "blocked", "failed", "running", "queued"}
            trigger_result["job"] = wake_result
            if follow_logs and str(wake_result.get("job_id") or "").strip():
                trigger_result["follow"] = _follow_worker_job(
                    store,
                    slug,
                    str(wake_result["job_id"]).strip(),
                    label="wake",
                    tail_logs=False,
                )
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
        slug, raw_name, goal, mode, schedule_arg, auto_start, no_auto, follow, detach, archetype, animations = _parse_business_start_args(
            argv,
            usage=f'usage: takyon {command} [--live] [--no-auto] [--follow|--detach] [--archetype app|shopify|saas] [--animation] [--schedule "every 6h"] <business> [goal text]',
            auto_default=auto_default,
        )
        # Fail closed on operator identity at the create chokepoint. On a plane that declares
        # per-session identity (the dashboard plane — operator_identity_mode() == "enforce"), a create
        # MUST carry the authenticated Auth0 principal as the owner. If it does not resolve, refusing
        # here is the upstream fix for both create-time identity failures: without it the
        # business.upsert below silently falls back to the platform owner (control_plane), so the row
        # gets an owner the dashboard user can never see, and the ceo_bootstrap worker then binds that
        # foreign/absent owner — surfacing downstream as "operator identity required" / a build that
        # operates a business the creator does not own. The legacy single-operator/dev planes
        # (operator_identity_mode() == "") keep their historical platform-owner fallback.
        if auto_start and not resolved_operator_user_id and operator_identity_mode():
            raise SystemExit(
                "cannot create business: no operator identity is bound to this session. "
                "The dashboard create must carry the authenticated operator; re-authenticate and retry."
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
        # period allowance on create through the billing rail. The reserve happens before the
        # business.upsert commit, but settlement is deferred until the business row is durably
        # visible; if create fails in between, the hold is released so the operator is not stranded.
        # Fails CLOSED for an operator under the floor regardless of --test/--no-auto (a create still
        # needs balance authority), and is idempotent on the slug so a retried create never
        # double-charges.
        # Fail-open only for identity-less / non-Postgres dev runs. The dashboard RPC's own call is
        # redundant-but-harmless. Raises InsufficientOperatorBalance (TakyonError subclass) which the
        # dashboard maps to the 4030 balance block.
        create_charge = _operator_create_balance_preflight(
            resolved_operator_user_id,
            business_slug=slug,
            defer_settle=True,
        )
        try:
            config = _read_model_config(store)
            if auto_start and not no_auto:
                _require_agent_model_config(config, model_override=model)
            auto_wake = _config_bool(config.get("auto_schedule_ceo_on_create"), default=False)
            schedule = schedule_arg or (config.get("default_ceo_schedule") or "every 6h").strip()
            should_schedule = bool(schedule_arg) or (not no_auto and (auto_start or auto_wake))
            upsert_op: dict[str, Any] = {
                "action": "business.upsert",
                "business": slug,
                "name": raw_name,
                "goal": goal,
                "mode": mode,
                # The archetype toggle (app|shopify|saas). Only sent when explicitly picked —
                # absent lets the store/DB default (web_saas) stay authoritative.
                **({"archetype": archetype} if archetype else {}),
                # Opt-in landing-hero animations (--animation). Persisted on the fresh business row's
                # metadata so the deferred ceo_bootstrap worker can read it and add ONE hero-motion
                # directive to the 2a landing pass. Only sent when the flag is set — a normal create
                # writes no such key and the bootstrap prose is unchanged.
                **({"metadata": {"landing_animations": True}} if animations else {}),
                # Fresh create only needs the business row before bootstrap can start. The initial
                # stub workspace (empty roots + seeded strategy) is non-authoritative and the
                # bootstrap worker will commit the real first revision; skipping the create-time
                # first-workspace sync keeps `/create` from wedging before it can even enqueue.
                "skip_initial_workspace_sync": True,
            }
            business_result = store.commit(
                scope=_scope_for_business(slug),
                operations=[upsert_op],
                idempotency_key=_idempotency_key("operator-init-v6", slug, mode or "keep", goal),
                reason="operator initialized business",
                actor="operator",
            )
            # `takyon create` must enqueue the first bootstrap job immediately after the durable
            # business write. A fresh-business `summary` read triggers projection/live-truth work
            # that can stall the operator rail before any bootstrap job exists, so use the durable
            # commit receipt itself as the persistence proof at this chokepoint.
            if not _business_upsert_commit_persisted_slug(business_result, slug):
                raise RuntimeError(f"business creation did not persist for {slug}")
            _operator_create_balance_finalize(create_charge, settle=True)
            create_charge = None
        except BaseException:
            try:
                _operator_create_balance_finalize(create_charge, settle=False)
            except Exception:
                pass
            raise
        active_mode = "live"
        if auto_start:
            bootstrap_job = _enqueue_pg_ceo_bootstrap(
                store,
                slug,
                goal=goal,
                mode=active_mode,
                schedule=schedule if should_schedule else None,
                max_turns=_clamp_bootstrap_max_turns(goal, max_turns, archetype=str(archetype or "")),
            )
            should_follow = (follow or follow_logs) and not detach
            bootstrap_job_id = str(bootstrap_job.get("job_id") or "").strip()
            if should_follow and bootstrap_job_id:
                print(
                    f"[bootstrap] queued job {bootstrap_job_id} for business:{slug}; "
                    "attaching after starter credit seed...",
                    flush=True,
                )
            # Free starter creative-credit seed is useful, but it must never sit between the durable
            # business row and the durable bootstrap job. If the creative-credit ledger is temporarily
            # unavailable, bootstrap still starts and records the precise blocker when it reaches
            # spendful creative work.
            starter_credit_seed = _try_seed_business_free_credits(
                slug,
                operator_user_id=resolved_operator_user_id,
            )
            # `--follow` or console/log mode: read-only live tail of the create-time bootstrap
            # until the worker-run job is terminal. Pure observation: it never claims or runs
            # the job, so it changes no creation/billing/identity authority and the build keeps
            # running if the operator detaches.
            follow_result = None
            if should_follow and bootstrap_job_id:
                follow_result = _follow_worker_job(
                    store,
                    slug,
                    bootstrap_job_id,
                    label="bootstrap",
                    tail_logs=not follow_logs,
                )
            return {
                "success": True,
                "business": slug,
                "mode": active_mode,
                "schedule": schedule if should_schedule else "",
                "init": business_result,
                "bootstrap_job": bootstrap_job,
                "detached": detach,
                "starter_credit_seed": starter_credit_seed,
                **({"follow": follow_result} if follow_result is not None else {}),
            }
        starter_credit_seed = _try_seed_business_free_credits(
            slug,
            operator_user_id=resolved_operator_user_id,
        )
        if not should_schedule:
            if isinstance(business_result, dict):
                return {**business_result, "starter_credit_seed": starter_credit_seed}
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
            "starter_credit_seed": starter_credit_seed,
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
            follow_logs=follow_logs,
            raw_agent=raw_agent,
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
            follow_logs=follow_logs,
            raw_agent=raw_agent,
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
        follow_logs=follow_logs,
        raw_agent=raw_agent,
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
            follow_logs=bool(getattr(args, "follow_logs", False)),
            raw_agent=bool(getattr(args, "raw_agent", False)),
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
