"""Takyon agent-map dashboard plugin backend.

Mounted at /api/plugins/takyon-map/ by the Takyon dashboard.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from agent.skill_utils import parse_frontmatter
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


router = APIRouter()

DASHBOARD_ROOT = Path(__file__).resolve().parent
PLUGIN_ROOT = DASHBOARD_ROOT.parent
PROJECT_ROOT = PLUGIN_ROOT.parent.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent

SKILLS_ROOT = PROJECT_ROOT / "skills" / "takyon"
HARNESS_COMMANDS_ROOT = PLUGIN_ROOT / "harness" / "commands"

CEO_PROMPT_PATH = PLUGIN_ROOT / "prompts" / "ceo.md"
CLI_PATH = PLUGIN_ROOT / "cli.py"
CORE_PATH = PLUGIN_ROOT / "core.py"
HARNESS_SETTINGS_PATH = PLUGIN_ROOT / "harness" / "settings.json"
CRON_JOBS_PY = PROJECT_ROOT / "cron" / "jobs.py"
CRON_SCHEDULER_PY = PROJECT_ROOT / "cron" / "scheduler.py"
ROOT_LAUNCHER = WORKSPACE_ROOT / "takyon"
HERMES_LAUNCHER = PROJECT_ROOT / "takyon"
BUILD_SKILLS_INDEX_PATH = PROJECT_ROOT / "scripts" / "build_takyon_skills_index.py"

EXACT_EDITABLE = {
    CEO_PROMPT_PATH.resolve(),
    CLI_PATH.resolve(),
    CORE_PATH.resolve(),
    HARNESS_SETTINGS_PATH.resolve(),
    CRON_JOBS_PY.resolve(),
    CRON_SCHEDULER_PY.resolve(),
    BUILD_SKILLS_INDEX_PATH.resolve(),
}


class SourceUpdate(BaseModel):
    path: str
    content: str
    expected_sha256: Optional[str] = None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _cron_jobs_file() -> Path:
    try:
        from takyon_constants import get_takyon_home

        return get_takyon_home().resolve() / "cron" / "jobs.json"
    except Exception:
        raw = os.environ.get("TAKYON_HOME") or str(Path.home() / ".takyon")
        return Path(raw).expanduser().resolve() / "cron" / "jobs.json"


def _exact_readable_files() -> set[Path]:
    files = {
        CEO_PROMPT_PATH.resolve(),
        CLI_PATH.resolve(),
        CORE_PATH.resolve(),
        HARNESS_SETTINGS_PATH.resolve(),
        CRON_JOBS_PY.resolve(),
        CRON_SCHEDULER_PY.resolve(),
        ROOT_LAUNCHER.resolve(),
        HERMES_LAUNCHER.resolve(),
        BUILD_SKILLS_INDEX_PATH.resolve(),
    }
    jobs_file = _cron_jobs_file()
    if jobs_file.exists():
        files.add(jobs_file.resolve())
    return files


def _is_skill_file(path: Path) -> bool:
    return path.name == "SKILL.md" and _is_inside(path, SKILLS_ROOT)


def _is_harness_command_file(path: Path) -> bool:
    return path.suffix == ".md" and _is_inside(path, HARNESS_COMMANDS_ROOT)


def _is_readable(path: Path) -> bool:
    resolved = path.resolve()
    return (
        resolved in _exact_readable_files()
        or _is_skill_file(resolved)
        or _is_harness_command_file(resolved)
    )


def _is_editable(path: Path) -> bool:
    resolved = path.resolve()
    return resolved in EXACT_EDITABLE or _is_skill_file(resolved) or _is_harness_command_file(resolved)


def _resolve_source_path(raw_path: str) -> Path:
    if not raw_path or "\x00" in raw_path:
        raise HTTPException(status_code=400, detail="invalid source path")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    resolved = path.resolve()
    if not _is_readable(resolved):
        raise HTTPException(status_code=403, detail="source path is outside the Takyon map allowlist")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="source file not found")
    return resolved


def _relative(path: Path) -> str:
    resolved = path.resolve()
    for root in (WORKSPACE_ROOT.resolve(), PROJECT_ROOT.resolve()):
        try:
            return str(resolved.relative_to(root))
        except ValueError:
            pass
    return str(resolved)


def _source_ref(path: Path, kind: str, label: str) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "relative_path": _relative(resolved),
        "kind": kind,
        "label": label,
        "editable": _is_editable(resolved),
        "exists": resolved.exists(),
    }


def _parse_frontmatter(text: str) -> dict[str, Any]:
    try:
        frontmatter, _ = parse_frontmatter(text)
    except Exception:
        return {}
    if not isinstance(frontmatter, dict):
        return {}
    return frontmatter


def _function_span(path: Path, name: str, class_name: str | None = None) -> tuple[int | None, int | None]:
    try:
        tree = ast.parse(_read_text(path))
    except Exception:
        return None, None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if class_name:
                parent_class = _parent_class_name(tree, node)
                if parent_class != class_name:
                    continue
            return node.lineno, getattr(node, "end_lineno", node.lineno)
    return None, None


def _parent_class_name(tree: ast.AST, target: ast.AST) -> str | None:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    current = parents.get(target)
    while current is not None:
        if isinstance(current, ast.ClassDef):
            return current.name
        current = parents.get(current)
    return None


def _line_for_pattern(path: Path, pattern: str) -> int | None:
    try:
        for index, line in enumerate(_read_text(path).splitlines(), start=1):
            if pattern in line:
                return index
    except Exception:
        return None
    return None


def _registry_snapshot() -> tuple[dict[str, Any], list[str]]:
    return {"version": None, "priority_bands": {}, "categories": {}, "tools": [], "skills": []}, ["takyon registry removed; using Hermes skill files directly"]


def _load_harness_settings() -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    try:
        return json.loads(_read_text(HARNESS_SETTINGS_PATH)), warnings
    except Exception as exc:
        warnings.append(f"harness settings read failed: {exc}")
        return {}, warnings


def _list_harness_commands() -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    if not HARNESS_COMMANDS_ROOT.exists():
        return commands
    for path in sorted(HARNESS_COMMANDS_ROOT.glob("*.md")):
        text = _read_text(path)
        meta = _parse_frontmatter(text)
        commands.append(
            {
                "name": path.stem,
                "description": meta.get("description") or "",
                "requires_business": str(meta.get("requires_business") or "true").lower() != "false",
                "priority_band": meta.get("priority_band") or "",
                "allowed_tools": meta.get("allowed_tools") or [],
                "path": str(path.resolve()),
            }
        )
    return commands


def _list_skill_files() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not SKILLS_ROOT.exists():
        return result
    for path in sorted(SKILLS_ROOT.glob("*/SKILL.md")):
        text = _read_text(path)
        meta = _parse_frontmatter(text)
        slug = path.parent.name
        skill_ref = str(meta.get("name") or slug)
        result[skill_ref] = {
            "name": meta.get("name") or skill_ref,
            "description": meta.get("description") or "",
            "slug": slug,
            "path": str(path.resolve()),
            "content": text,
            "frontmatter": meta,
        }
    return result


def _load_cron_jobs() -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    try:
        from cron.jobs import list_jobs

        return list_jobs(include_disabled=True), warnings
    except Exception as exc:
        warnings.append(f"cron job read failed: {exc}")
        jobs_file = _cron_jobs_file()
        if not jobs_file.exists():
            return [], warnings
        try:
            raw = json.loads(_read_text(jobs_file))
            if isinstance(raw, list):
                return raw, warnings
            if isinstance(raw, dict) and isinstance(raw.get("jobs"), list):
                return raw["jobs"], warnings
        except Exception as inner_exc:
            warnings.append(f"cron jobs.json fallback failed: {inner_exc}")
    return [], warnings


def _add_source(sources: dict[str, dict[str, Any]], path: Path, kind: str, label: str) -> None:
    sources[str(path.resolve())] = _source_ref(path, kind, label)


def _add_node(
    nodes: list[dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    *,
    node_id: str,
    label: str,
    kind: str,
    lane: str,
    description: str = "",
    source_path: Path | None = None,
    source_kind: str = "source",
    line_start: int | None = None,
    line_end: int | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if source_path is not None:
        _add_source(sources, source_path, source_kind, label)
    nodes.append(
        {
            "id": node_id,
            "label": label,
            "kind": kind,
            "lane": lane,
            "description": description,
            "source_path": str(source_path.resolve()) if source_path else None,
            "line_start": line_start,
            "line_end": line_end,
            "tags": tags or [],
            "metadata": metadata or {},
        }
    )


def _add_edge(
    edges: list[dict[str, Any]],
    edge_keys: set[tuple[str, str, str]],
    source: str,
    target: str,
    label: str,
    kind: str = "flow",
) -> None:
    key = (source, target, label)
    if key in edge_keys:
        return
    edge_keys.add(key)
    edges.append({"source": source, "target": target, "label": label, "kind": kind})


def _tool_category_id(category: str) -> str:
    return f"tool-category:{category}"


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "plugin": "takyon-map"}


@router.get("/graph")
async def graph() -> dict[str, Any]:
    sources: dict[str, dict[str, Any]] = {}
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    edge_keys: set[tuple[str, str, str]] = set()
    warnings: list[str] = []

    registry, registry_warnings = _registry_snapshot()
    warnings.extend(registry_warnings)
    settings, settings_warnings = _load_harness_settings()
    warnings.extend(settings_warnings)
    harness_commands = _list_harness_commands()
    skill_files = _list_skill_files()
    cron_jobs, cron_warnings = _load_cron_jobs()
    warnings.extend(cron_warnings)

    _add_node(
        nodes,
        sources,
        node_id="workspace-launcher",
        label="./takyon",
        kind="entrypoint",
        lane="entry",
        description="Workspace entrypoint that sets TAKYON_HOME to the workspace .takyon directory and launches the Hermes trunk.",
        source_path=ROOT_LAUNCHER,
        source_kind="entrypoint",
        tags=["operator", "TAKYON_HOME"],
    )
    _add_node(
        nodes,
        sources,
        node_id="hermes-launcher",
        label="hermes-agent-main/takyon",
        kind="entrypoint",
        lane="entry",
        description="Hermes/Takyon launcher that resolves Python and imports plugins.takyon.cli.",
        source_path=HERMES_LAUNCHER,
        source_kind="entrypoint",
        tags=["runtime"],
    )
    _add_edge(edges, edge_keys, "workspace-launcher", "hermes-launcher", "execs")

    _add_node(
        nodes,
        sources,
        node_id="shell-settings",
        label="Shell settings",
        kind="config",
        lane="shell",
        description="Shell controls, palette visibility, thinking indicator, progress, and shell history tuning.",
        source_path=HARNESS_SETTINGS_PATH,
        source_kind="harness",
        tags=["source of truth", "slash commands"],
        metadata={
            "control_commands": settings.get("controlCommands") or [],
            "default_visible": ((settings.get("ui") or {}).get("slashPalette") or {}).get("defaultVisible") or [],
            "shell_history": ((settings.get("ui") or {}).get("shellHistory") or {}),
        },
    )
    _add_edge(edges, edge_keys, "hermes-launcher", "shell-settings", "loads")

    for function_name, label, node_id, desc in [
        ("_operator_context_message", "Scope wrapper", "operator-context", "Wraps plain text with global or business scope before the CEO sees it."),
        ("_run_agent", "Initial CEO run prompt", "manual-ceo-prompt", "Builds the manual-turn operator prompt and passes the Takyon CEO prompt as the ephemeral system prompt."),
        ("_business_bootstrap_instruction", "Create bootstrap prompt", "bootstrap-prompt", "Operational /create bootstrap instruction used by create/build when auto-starting a business."),
        ("_queue_skill_invocation", "Skill invocation wrapper", "skill-invocation-wrapper", "Loads a Hermes skill and wraps the operator instruction for direct skill invocation."),
        ("_load_ceo_prompt", "CEO prompt loader", "ceo-prompt-loader", "Loads the stable Takyon CEO prompt file."),
    ]:
        start, end = _function_span(CLI_PATH, function_name)
        _add_node(
            nodes,
            sources,
            node_id=node_id,
            label=label,
            kind="prompt-wrapper",
            lane="prompts",
            description=desc,
            source_path=CLI_PATH,
            source_kind="runtime prompt",
            line_start=start,
            line_end=end,
            tags=["prompt", "cli.py"],
            metadata={"function": function_name},
        )
    _add_edge(edges, edge_keys, "shell-settings", "operator-context", "plain text")
    _add_edge(edges, edge_keys, "operator-context", "manual-ceo-prompt", "manual turn")
    _add_edge(edges, edge_keys, "bootstrap-prompt", "manual-ceo-prompt", "create/build")
    _add_edge(edges, edge_keys, "skill-invocation-wrapper", "manual-ceo-prompt", "queued into session")

    _add_node(
        nodes,
        sources,
        node_id="prompt:ceo",
        label="Takyon CEO prompt",
        kind="prompt",
        lane="ceo",
        description="Stable Takyon CEO runtime prompt. Wakes and creates add small invocation overlays instead of swapping prompt variants.",
        source_path=CEO_PROMPT_PATH,
        source_kind="runtime prompt",
        tags=["source of truth"],
        metadata={
            "skills_root": str(SKILLS_ROOT),
            "build_index_script": str(BUILD_SKILLS_INDEX_PATH),
        },
    )
    _add_edge(edges, edge_keys, "manual-ceo-prompt", "prompt:ceo", "ephemeral system prompt")
    _add_edge(edges, edge_keys, "ceo-prompt-loader", "prompt:ceo", "loads")

    for skill_ref, file_info in skill_files.items():
        _add_node(
            nodes,
            sources,
            node_id=f"skill:{skill_ref}",
            label=skill_ref,
            kind="skill",
            lane="skills",
            description=file_info.get("description") or "Takyon Hermes skill",
            source_path=Path(file_info["path"]),
            source_kind="skill",
            tags=["takyon"],
            metadata={"frontmatter": file_info.get("frontmatter") or {}},
        )
    _add_node(
        nodes,
        sources,
        node_id="tool:business_schedule_ceo_wakeup",
        label="business_schedule_ceo_wakeup",
        kind="tool",
        lane="wakeups",
        description="Create or update a CEO wake cron job.",
        source_path=CORE_PATH,
        source_kind="runtime",
        tags=["cron", "wakeup", "business_*"],
        metadata={},
    )

    for function_name, label, node_id, desc, src in [
        ("handle_business_schedule_ceo_wakeup", "Schedule wakeup handler", "core-schedule-wakeup-handler", "business_schedule_ceo_wakeup commits cron.ensure_ceo_wakeup.", CORE_PATH),
        ("_ensure_ceo_cron", "Ensure CEO cron", "core-ensure-ceo-cron", "Creates or updates the takyon-ceo:<business> cron job.", CORE_PATH),
        ("_ceo_cron_prompt", "CEO wakeup prompt", "core-ceo-cron-prompt", "Source prompt stored into CEO wake cron jobs.", CORE_PATH),
        ("_build_job_prompt", "Cron prompt assembler", "cron-build-job-prompt", "Scheduler wrapper that prepends cron guidance and loads attached skills.", CRON_SCHEDULER_PY),
        ("_scan_assembled_cron_prompt", "Cron injection scan", "cron-injection-scan", "Scans assembled cron prompts including loaded skill content.", CRON_SCHEDULER_PY),
        ("create_job", "Cron job create", "cron-create-job", "Canonical cron job creator and skill field normalizer entrypoint.", CRON_JOBS_PY),
        ("_apply_skill_fields", "Cron skill fields", "cron-skill-fields", "Aligns legacy skill and canonical skills fields in cron jobs.", CRON_JOBS_PY),
    ]:
        start, end = _function_span(src, function_name)
        _add_node(
            nodes,
            sources,
            node_id=node_id,
            label=label,
            kind="wakeup" if "cron" in node_id or "wakeup" in node_id else "runtime",
            lane="wakeups",
            description=desc,
            source_path=src,
            source_kind="runtime",
            line_start=start,
            line_end=end,
            tags=["cron", "wakeup"] if "cron" in node_id or "wakeup" in node_id else [],
            metadata={"function": function_name},
        )

    _add_edge(edges, edge_keys, "tool:business_schedule_ceo_wakeup", "core-schedule-wakeup-handler", "tool handler")
    _add_edge(edges, edge_keys, "core-schedule-wakeup-handler", "core-ensure-ceo-cron", "commits operation")
    _add_edge(edges, edge_keys, "core-ensure-ceo-cron", "core-ceo-cron-prompt", "writes prompt")
    _add_edge(edges, edge_keys, "core-ensure-ceo-cron", "cron-create-job", "create/update")
    _add_edge(edges, edge_keys, "cron-create-job", "cron-skill-fields", "stores skills")
    _add_edge(edges, edge_keys, "cron-skill-fields", "cron-build-job-prompt", "runtime load")
    _add_edge(edges, edge_keys, "cron-build-job-prompt", "cron-injection-scan", "guard")
    _add_edge(edges, edge_keys, "cron-build-job-prompt", "prompt:ceo", "uses stable CEO prompt")

    jobs_file = _cron_jobs_file()
    if jobs_file.exists():
        _add_node(
            nodes,
            sources,
            node_id="cron-jobs-store",
            label="Cron jobs store",
            kind="state",
            lane="wakeups",
            description="Current cron jobs from TAKYON_HOME/cron/jobs.json. Read-only here; schedule through Takyon tools/commands.",
            source_path=jobs_file,
            source_kind="cron state",
            tags=["TAKYON_HOME", "read-only"],
            metadata={"job_count": len(cron_jobs), "takyon_home": str(jobs_file.parent.parent)},
        )
        _add_edge(edges, edge_keys, "cron-create-job", "cron-jobs-store", "persists")
        _add_edge(edges, edge_keys, "cron-jobs-store", "cron-build-job-prompt", "scheduler reads")

    for job in cron_jobs[:60]:
        name = str(job.get("name") or job.get("id") or "cron job")
        skills = job.get("skills")
        if skills is None:
            skills = [job.get("skill")] if job.get("skill") else []
        if isinstance(skills, str):
            skills = [skills]
        skills = [str(item) for item in skills if item]
        is_ceo_job = name.startswith("takyon-ceo:")
        if not is_ceo_job:
            continue
        job_id = str(job.get("id") or name)
        node_id = f"cron-job:{job_id}"
        _add_node(
            nodes,
            sources,
            node_id=node_id,
            label=name,
            kind="cron-job",
            lane="wakeups",
            description=str(job.get("schedule_display") or job.get("schedule") or "scheduled wake"),
            source_path=jobs_file if jobs_file.exists() else None,
            source_kind="cron state",
            tags=["current", *skills],
            metadata=job,
        )
        if jobs_file.exists():
            _add_edge(edges, edge_keys, "cron-jobs-store", node_id, "contains")
        _add_edge(edges, edge_keys, node_id, "cron-build-job-prompt", "fires through")

    for command in harness_commands:
        path = Path(command["path"])
        node_id = f"harness-command:{command['name']}"
        _add_node(
            nodes,
            sources,
            node_id=node_id,
            label=f"/{command['name']}",
            kind="harness-command",
            lane="shell",
            description=command.get("description") or "",
            source_path=path,
            source_kind="harness command",
            tags=[command.get("priority_band") or "unbanded"],
            metadata=command,
        )
        _add_edge(edges, edge_keys, "shell-settings", node_id, "file-backed command")
        _add_edge(edges, edge_keys, node_id, "manual-ceo-prompt", "renders into CEO turn")

    relevant_controls = {"create", "wake", "skills-index", "skills", "commands", "cron", "run", "goal"}
    for command in settings.get("controlCommands") or []:
        name = str(command.get("name") or "").strip()
        if name not in relevant_controls:
            continue
        node_id = f"control-command:{name}"
        _add_node(
            nodes,
            sources,
            node_id=node_id,
            label=f"/{name}",
            kind="control-command",
            lane="shell",
            description=str(command.get("description") or ""),
            source_path=HARNESS_SETTINGS_PATH,
            source_kind="harness",
            tags=[str(command.get("priorityBand") or "unbanded")],
            metadata=command,
        )
        _add_edge(edges, edge_keys, "shell-settings", node_id, "control")
        if name == "create":
            _add_edge(edges, edge_keys, node_id, "bootstrap-prompt", "auto-start")
        elif name == "wake":
            _add_edge(edges, edge_keys, node_id, "tool:business_schedule_ceo_wakeup", "schedules")
        elif name in {"run", "goal"}:
            _add_edge(edges, edge_keys, node_id, "manual-ceo-prompt", "manual instruction")
        elif name == "skills-index":
            _add_edge(edges, edge_keys, node_id, "prompt:ceo", "rebuilds skill discovery context")

    # Derive cross-skill and skill-to-tool references from the actual skill text.
    tool_names = ["business_calculate_pulse", "business_schedule_ceo_wakeup", "business_claude_agent_task", "business_publish_outreach"]
    skill_refs = sorted(skill_files.keys(), key=len, reverse=True)
    for skill_ref, file_info in skill_files.items():
        source_id = f"skill:{skill_ref}"
        content = file_info.get("content") or ""
        for other_ref in skill_refs:
            if other_ref == skill_ref:
                continue
            if other_ref in content:
                _add_edge(edges, edge_keys, source_id, f"skill:{other_ref}", "mentions")
        for tool_name in tool_names:
            if re.search(rf"\b{re.escape(tool_name)}\b", content):
                _add_edge(edges, edge_keys, source_id, "tool:business_schedule_ceo_wakeup" if tool_name == "business_schedule_ceo_wakeup" else "manual-ceo-prompt", f"mentions {tool_name}", "reference")

    if "skill:takyon-business-metrics" in {node["id"] for node in nodes}:
        _add_edge(edges, edge_keys, "core-ceo-cron-prompt", "skill:takyon-business-metrics", "wake step")
        _add_edge(edges, edge_keys, "prompt:ceo", "skill:takyon-business-metrics", "wake protocol")
    _add_edge(edges, edge_keys, "prompt:ceo", "tool:business_schedule_ceo_wakeup", "can schedule next wake")

    return {
        "version": 1,
        "workspace_root": str(WORKSPACE_ROOT),
        "project_root": str(PROJECT_ROOT),
        "takyon_home": str(_cron_jobs_file().parent.parent),
        "generated_from": {
            "ceo_prompt": str(CEO_PROMPT_PATH),
            "skills_root": str(SKILLS_ROOT),
            "skills_index_build": str(BUILD_SKILLS_INDEX_PATH),
            "harness_settings": str(HARNESS_SETTINGS_PATH),
            "harness_commands": str(HARNESS_COMMANDS_ROOT),
            "cron_jobs": str(CRON_JOBS_PY),
            "cron_scheduler": str(CRON_SCHEDULER_PY),
            "cron_state": str(_cron_jobs_file()),
        },
        "summary": {
            "nodes": len(nodes),
            "edges": len(edges),
            "skills_registered": len(skill_files),
            "skill_files": len(skill_files),
            "tools": len(tool_names),
            "harness_commands": len(harness_commands),
            "cron_jobs": len(cron_jobs),
        },
        "warnings": warnings,
        "sources": sorted(sources.values(), key=lambda item: item["relative_path"]),
        "nodes": nodes,
        "edges": edges,
    }


@router.get("/source")
async def source(path: str) -> dict[str, Any]:
    source_path = _resolve_source_path(path)
    content = _read_text(source_path)
    stat = source_path.stat()
    return {
        "path": str(source_path),
        "relative_path": _relative(source_path),
        "content": content,
        "sha256": _sha256(content),
        "editable": _is_editable(source_path),
        "mtime": stat.st_mtime,
        "size": stat.st_size,
    }


@router.put("/source")
async def update_source(body: SourceUpdate) -> dict[str, Any]:
    source_path = _resolve_source_path(body.path)
    if not _is_editable(source_path):
        raise HTTPException(status_code=403, detail="source file is read-only in the agent map")
    current = _read_text(source_path)
    current_sha = _sha256(current)
    if body.expected_sha256 and body.expected_sha256 != current_sha:
        raise HTTPException(status_code=409, detail="source changed on disk; reload before saving")

    if source_path.suffix == ".json":
        try:
            json.loads(body.content)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}") from exc
    if source_path.suffix == ".py":
        try:
            ast.parse(body.content)
        except SyntaxError as exc:
            raise HTTPException(status_code=400, detail=f"invalid Python: {exc}") from exc

    temp_path = source_path.with_name(f".{source_path.name}.takyon-map-{os.getpid()}.tmp")
    try:
        temp_path.write_text(body.content, encoding="utf-8")
        try:
            temp_path.chmod(source_path.stat().st_mode & 0o777)
        except OSError:
            pass
        os.replace(temp_path, source_path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

    saved = _read_text(source_path)
    return {
        "ok": True,
        "path": str(source_path),
        "relative_path": _relative(source_path),
        "sha256": _sha256(saved),
        "size": source_path.stat().st_size,
    }
