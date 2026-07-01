"""Shared slash command helpers for skills.

Shared between CLI (cli.py) and gateway (gateway/run.py) so both surfaces
can invoke skills via /skill-name commands.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from agent.skill_utils import (
    extract_skill_description,
    get_all_skills_dirs,
    get_disabled_skill_names,
    parse_frontmatter,
    skill_matches_platform,
)
from takyon_constants import display_takyon_home
from agent.skill_preprocessing import (
    expand_inline_shell as _expand_inline_shell,
    load_skills_config as _load_skills_config,
    substitute_template_vars as _substitute_template_vars,
)

logger = logging.getLogger(__name__)

_skill_commands: Dict[str, Dict[str, Any]] = {}
_skill_commands_platform: Optional[str] = None
# Patterns for sanitizing skill names into clean hyphen-separated slugs.
_SKILL_INVALID_CHARS = re.compile(r"[^a-z0-9-]")
_SKILL_MULTI_HYPHEN = re.compile(r"-{2,}")


def _resolve_skill_commands_platform() -> Optional[str]:
    """Return the current platform scope used for disabled-skill filtering.

    Used to detect when the active platform has shifted so
    :func:`get_skill_commands` can drop a stale cache that was populated
    for a different platform's ``skills.platform_disabled`` view (#14536).

    Resolves from (in order) ``TAKYON_PLATFORM`` env var and
    ``TAKYON_SESSION_PLATFORM`` from the gateway session context. Returns
    ``None`` when no platform scope is active (e.g. classic CLI, RL
    rollouts, standalone scripts).
    """
    try:
        from gateway.session_context import get_session_env

        resolved_platform = (
            os.getenv("TAKYON_PLATFORM")
            or get_session_env("TAKYON_SESSION_PLATFORM")
        )
    except Exception:
        resolved_platform = os.getenv("TAKYON_PLATFORM")
    return resolved_platform or None

def _load_skill_payload(skill_identifier: str, task_id: str | None = None) -> tuple[dict[str, Any], Path | None, str] | None:
    """Load a skill by name/path and return (loaded_payload, skill_dir, display_name)."""
    raw_identifier = (skill_identifier or "").strip()
    if not raw_identifier:
        return None

    identifier = raw_identifier.lstrip("/")
    identifier_path = Path(raw_identifier).expanduser()
    skill_file: Path | None = None
    scan_dirs = get_all_skills_dirs()

    if identifier_path.is_absolute():
        if identifier_path.is_dir():
            candidate = identifier_path / "SKILL.md"
            if candidate.exists():
                skill_file = candidate
        elif identifier_path.name == "SKILL.md" and identifier_path.exists():
            skill_file = identifier_path
    else:
        for skills_dir in scan_dirs:
            direct = skills_dir / identifier / "SKILL.md"
            if direct.exists():
                skill_file = direct
                break

    # Fast path: consult the already-populated slash-command index
    # (name/slug -> skill_md_path) before doing an O(N) rglob + YAML
    # re-parse across every skills dir (#4241).
    if skill_file is None:
        try:
            commands = get_skill_commands()
            slug = identifier.lower().replace(" ", "-").replace("_", "-")
            slug = _SKILL_INVALID_CHARS.sub("", slug)
            slug = _SKILL_MULTI_HYPHEN.sub("-", slug).strip("-")
            candidates = []
            if slug:
                candidates.append(commands.get(f"/{slug}"))
            for info in commands.values():
                if str((info or {}).get("name") or "").strip() == identifier:
                    candidates.append(info)
                    break
            for info in candidates:
                if not info:
                    continue
                candidate = Path(str(info.get("skill_md_path") or ""))
                if candidate.exists():
                    skill_file = candidate
                    break
        except Exception:
            skill_file = None

    if skill_file is None:
        for skills_dir in scan_dirs:
            for candidate in skills_dir.rglob("SKILL.md"):
                try:
                    raw = candidate.read_text(encoding="utf-8")
                    frontmatter, body = parse_frontmatter(raw)
                except Exception:
                    continue
                name = str(frontmatter.get("name") or candidate.parent.name).strip()
                rel_dir = str(candidate.parent.relative_to(skills_dir)).replace("\\", "/")
                if identifier in {name, candidate.parent.name, rel_dir}:
                    skill_file = candidate
                    break
            if skill_file is not None:
                break

    if skill_file is None or not skill_file.exists():
        return None

    raw = skill_file.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(raw)
    skill_name = str(frontmatter.get("name") or skill_file.parent.name).strip() or skill_file.parent.name
    linked_files: dict[str, list[str]] = {}
    for subdir in ("references", "templates", "scripts", "assets"):
        subdir_path = skill_file.parent / subdir
        if not subdir_path.exists():
            continue
        linked_files[subdir] = [
            str(path.relative_to(skill_file.parent))
            for path in sorted(subdir_path.rglob("*"))
            if path.is_file() and not path.is_symlink()
        ]
    loaded_skill = {
        "success": True,
        "name": skill_name,
        "path": str(skill_file),
        "skill_dir": str(skill_file.parent),
        "content": body.strip(),
        "raw_content": raw,
        "linked_files": linked_files,
        "setup_needed": False,
        "setup_skipped": False,
        "gateway_setup_hint": "",
        "setup_note": "",
    }
    return loaded_skill, skill_file.parent, skill_name


def _inject_skill_config(loaded_skill: dict[str, Any], parts: list[str]) -> None:
    """Resolve and inject skill-declared config values into the message parts.

    If the loaded skill's frontmatter declares ``metadata.takyon.config``
    entries, their current values (from config.yaml or defaults) are appended
    as a ``[Skill config: ...]`` block so the agent knows the configured values
    without needing to read config.yaml itself.
    """
    try:
        from agent.skill_utils import (
            extract_skill_config_vars,
            parse_frontmatter,
            resolve_skill_config_values,
        )

        # The loaded_skill dict contains the raw content which includes frontmatter
        raw_content = str(loaded_skill.get("raw_content") or loaded_skill.get("content") or "")
        if not raw_content:
            return

        frontmatter, _ = parse_frontmatter(raw_content)
        config_vars = extract_skill_config_vars(frontmatter)
        if not config_vars:
            return

        resolved = resolve_skill_config_values(config_vars)
        if not resolved:
            return

        lines = ["", f"[Skill config (from {display_takyon_home()}/config.yaml):"]
        for key, value in resolved.items():
            display_val = str(value) if value else "(not set)"
            lines.append(f"  {key} = {display_val}")
        lines.append("]")
        parts.extend(lines)
    except Exception:
        pass  # Non-critical — skill still loads without config injection


def _build_skill_message(
    loaded_skill: dict[str, Any],
    skill_dir: Path | None,
    activation_note: str,
    user_instruction: str = "",
    runtime_note: str = "",
    session_id: str | None = None,
) -> str:
    """Format a loaded skill into a user/system message payload."""
    from takyon_constants import get_skills_dir

    content = str(loaded_skill.get("content") or "")

    # ── Template substitution and inline-shell expansion ──
    # Done before anything else so downstream blocks (setup notes,
    # supporting-file hints) see the expanded content.
    skills_cfg = _load_skills_config()
    if skills_cfg.get("template_vars", True):
        content = _substitute_template_vars(content, skill_dir, session_id)
    if skills_cfg.get("inline_shell", False):
        timeout = int(skills_cfg.get("inline_shell_timeout", 10) or 10)
        content = _expand_inline_shell(content, skill_dir, timeout)

    parts = [activation_note, "", content.strip()]

    # ── Inject the absolute skill directory so the agent can reference
    #    bundled scripts without an extra skill_view() round-trip. ──
    if skill_dir:
        parts.append("")
        parts.append(f"[Skill directory: {skill_dir}]")
        parts.append(
            "Resolve any relative paths in this skill (e.g. `scripts/foo.js`, "
            "`templates/config.yaml`) against that directory, then run them "
            "with the terminal tool using the absolute path."
        )

    # ── Inject resolved skill config values ──
    _inject_skill_config(loaded_skill, parts)

    if loaded_skill.get("setup_skipped"):
        parts.extend(
            [
                "",
                "[Skill setup note: Required environment setup was skipped. Continue loading the skill and explain any reduced functionality if it matters.]",
            ]
        )
    elif loaded_skill.get("gateway_setup_hint"):
        parts.extend(
            [
                "",
                f"[Skill setup note: {loaded_skill['gateway_setup_hint']}]",
            ]
        )
    elif loaded_skill.get("setup_needed") and loaded_skill.get("setup_note"):
        parts.extend(
            [
                "",
                f"[Skill setup note: {loaded_skill['setup_note']}]",
            ]
        )

    supporting = []
    linked_files = loaded_skill.get("linked_files") or {}
    for entries in linked_files.values():
        if isinstance(entries, list):
            supporting.extend(entries)

    if not supporting and skill_dir:
        for subdir in ("references", "templates", "scripts", "assets"):
            subdir_path = skill_dir / subdir
            if subdir_path.exists():
                for f in sorted(subdir_path.rglob("*")):
                    if f.is_file() and not f.is_symlink():
                        rel = str(f.relative_to(skill_dir))
                        supporting.append(rel)

    if supporting and skill_dir:
        try:
            skill_view_target = str(skill_dir.relative_to(get_skills_dir()))
        except ValueError:
            # Skill is from an external dir — use the skill name instead
            skill_view_target = skill_dir.name
        parts.append("")
        parts.append("[This skill has supporting files:]")
        for sf in supporting:
            parts.append(f"- {sf}  ->  {skill_dir / sf}")
        parts.append(
            f'\nLoad any of these with skill_view(name="{skill_view_target}", '
            f'file_path="<path>"), or run scripts directly by absolute path '
            f"(e.g. `node {skill_dir}/scripts/foo.js`)."
        )

    if user_instruction:
        parts.append("")
        parts.append(f"The user has provided the following instruction alongside the skill invocation: {user_instruction}")

    if runtime_note:
        parts.append("")
        parts.append(f"[Runtime note: {runtime_note}]")

    return "\n".join(parts)


def scan_skill_commands() -> Dict[str, Dict[str, Any]]:
    """Scan ~/.takyon/skills/ and return a mapping of /command -> skill info.

    Returns:
        Dict mapping "/skill-name" to {name, description, skill_md_path, skill_dir}.
    """
    global _skill_commands, _skill_commands_platform
    _skill_commands_platform = _resolve_skill_commands_platform()
    _skill_commands = {}
    try:
        from agent.skill_utils import iter_skill_index_files

        disabled = get_disabled_skill_names()
        seen_names: set = set()

        dirs_to_scan = get_all_skills_dirs()

        for scan_dir in dirs_to_scan:
            for skill_md in iter_skill_index_files(scan_dir, "SKILL.md"):
                if any(part in {'.git', '.github', '.hub', '.archive'} for part in skill_md.parts):
                    continue
                try:
                    content = skill_md.read_text(encoding='utf-8')
                    frontmatter, body = parse_frontmatter(content)
                    # Skip skills incompatible with the current OS platform
                    if not skill_matches_platform(frontmatter):
                        continue
                    name = frontmatter.get('name', skill_md.parent.name)
                    if name in seen_names:
                        continue
                    # Respect user's disabled skills config
                    if name in disabled:
                        continue
                    description = frontmatter.get('description', '') or extract_skill_description(frontmatter)
                    if not description:
                        for line in body.strip().split('\n'):
                            line = line.strip()
                            if line and not line.startswith('#'):
                                description = line[:80]
                                break
                    seen_names.add(name)
                    # Normalize to hyphen-separated slug, stripping
                    # non-alnum chars (e.g. +, /) to avoid invalid
                    # Telegram command names downstream.
                    cmd_name = name.lower().replace(' ', '-').replace('_', '-')
                    cmd_name = _SKILL_INVALID_CHARS.sub('', cmd_name)
                    cmd_name = _SKILL_MULTI_HYPHEN.sub('-', cmd_name).strip('-')
                    if not cmd_name:
                        continue
                    _skill_commands[f"/{cmd_name}"] = {
                        "name": name,
                        "description": description or f"Invoke the {name} skill",
                        "skill_md_path": str(skill_md),
                        "skill_dir": str(skill_md.parent),
                    }
                except Exception:
                    continue
    except Exception:
        pass
    return _skill_commands


def get_skill_commands() -> Dict[str, Dict[str, Any]]:
    """Return the current skill commands mapping (scan first if empty).

    Rescans when the active platform scope changes (e.g. a gateway
    process serving Telegram and Discord concurrently) so each platform
    sees its own ``skills.platform_disabled`` view (#14536).
    """
    if (
        not _skill_commands
        or _skill_commands_platform != _resolve_skill_commands_platform()
    ):
        scan_skill_commands()
    return _skill_commands


def reload_skills() -> Dict[str, Any]:
    """Re-scan the skills directory and return a diff of what changed.

    Rescans ``~/.takyon/skills/`` and any ``skills.external_dirs`` so the
    slash-command map (``agent.skill_commands._skill_commands``) reflects
    skills added or removed on disk.

    This does NOT invalidate the skills system-prompt cache. Skills are
    called by name via ``/skill-name``, ``skills_list``, or ``skill_view``
    — they don't need to be in the system prompt for the model to use them.
    Keeping the prompt cache intact preserves prefix caching across the
    reload, so a user invoking ``/reload-skills`` pays no cache-reset cost.

    Returns:
        Dict with keys::

            {
              "added":      [{"name": str, "description": str}, ...],
              "removed":    [{"name": str, "description": str}, ...],
              "unchanged":  [skill names present before and after],
              "total":      total skill count after rescan,
              "commands":   total /slash-skill count after rescan,
            }

        ``description`` is the skill's full SKILL.md frontmatter
        ``description:`` field — the same string the system prompt renders
        as ``    - name: description`` for pre-existing skills.
    """
    # Snapshot pre-reload state (name -> description) from the current
    # slash-command cache. Using dicts lets the post-rescan diff carry
    # descriptions for newly-visible or just-removed skills without a
    # second disk walk.
    def _snapshot(cmds: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for slash_key, info in cmds.items():
            bare = slash_key.lstrip("/")
            out[bare] = (info or {}).get("description") or ""
        return out

    before = _snapshot(_skill_commands)

    # Rescan the skills dir. ``scan_skill_commands`` resets
    # ``_skill_commands = {}`` internally and repopulates it.
    new_commands = scan_skill_commands()

    after = _snapshot(new_commands)

    added_names = sorted(set(after) - set(before))
    removed_names = sorted(set(before) - set(after))
    unchanged = sorted(set(after) & set(before))

    added = [{"name": n, "description": after[n]} for n in added_names]
    # For removed skills, use the description we had cached pre-rescan
    # (the skill file is gone so we can't re-read it).
    removed = [{"name": n, "description": before[n]} for n in removed_names]

    return {
        "added": added,
        "removed": removed,
        "unchanged": unchanged,
        "total": len(after),
        "commands": len(new_commands),
    }


def resolve_skill_command_key(command: str) -> Optional[str]:
    """Resolve a user-typed /command to its canonical skill_cmds key.

    Skills are always stored with hyphens — ``scan_skill_commands`` normalizes
    spaces and underscores to hyphens when building the key. Hyphens and
    underscores are treated interchangeably in user input: this matches
    ``_check_unavailable_skill`` and accommodates Telegram bot-command names
    (which disallow hyphens, so ``/claude-code`` is registered as
    ``/claude_code`` and comes back in the underscored form).

    Returns the matching ``/slug`` key from ``get_skill_commands()`` or
    ``None`` if no match.
    """
    if not command:
        return None
    cmd_key = f"/{command.replace('_', '-')}"
    return cmd_key if cmd_key in get_skill_commands() else None


def build_skill_invocation_message(
    cmd_key: str,
    user_instruction: str = "",
    task_id: str | None = None,
    runtime_note: str = "",
) -> Optional[str]:
    """Build the user message content for a skill slash command invocation.

    Args:
        cmd_key: The command key including leading slash (e.g., "/gif-search").
        user_instruction: Optional text the user typed after the command.

    Returns:
        The formatted message string, or None if the skill wasn't found.
    """
    commands = get_skill_commands()
    skill_info = commands.get(cmd_key)
    if not skill_info:
        return None

    loaded = _load_skill_payload(skill_info["skill_dir"], task_id=task_id)
    if not loaded:
        return None

    loaded_skill, skill_dir, skill_name = loaded

    # Track active usage for Curator lifecycle management (#17782)
    try:
        from tools.skill_usage import bump_use
        bump_use(skill_name)
    except Exception:
        pass  # Non-critical — skill invocation proceeds regardless

    activation_note = (
        f'[IMPORTANT: The user has invoked the "{skill_name}" skill, indicating they want '
        "you to follow its instructions. The full skill content is loaded below.]"
    )
    return _build_skill_message(
        loaded_skill,
        skill_dir,
        activation_note,
        user_instruction=user_instruction,
        runtime_note=runtime_note,
        session_id=task_id,
    )


def build_preloaded_skills_prompt(
    skill_identifiers: list[str],
    task_id: str | None = None,
) -> tuple[str, list[str], list[str]]:
    """Load one or more skills for session-wide CLI preloading.

    Returns (prompt_text, loaded_skill_names, missing_identifiers).
    """
    prompt_parts: list[str] = []
    loaded_names: list[str] = []
    missing: list[str] = []

    seen: set[str] = set()
    for raw_identifier in skill_identifiers:
        identifier = (raw_identifier or "").strip()
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)

        loaded = _load_skill_payload(identifier, task_id=task_id)
        if not loaded:
            missing.append(identifier)
            continue

        loaded_skill, skill_dir, skill_name = loaded

        # Track active usage for Curator lifecycle management (#17782)
        try:
            from tools.skill_usage import bump_use
            bump_use(skill_name)
        except Exception:
            pass  # Non-critical

        activation_note = (
            f'[IMPORTANT: The user launched this CLI session with the "{skill_name}" skill '
            "preloaded. Treat its instructions as active guidance for the duration of this "
            "session unless the user overrides them.]"
        )
        prompt_parts.append(
            _build_skill_message(
                loaded_skill,
                skill_dir,
                activation_note,
                session_id=task_id,
            )
        )
        loaded_names.append(skill_name)

    return "\n\n".join(prompt_parts), loaded_names, missing
