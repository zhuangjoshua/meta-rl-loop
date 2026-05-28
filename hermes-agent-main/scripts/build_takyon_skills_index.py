#!/usr/bin/env python3
"""Sync Takyon bundled skills into the active profile and prebuild the skills index snapshot.

This gives Takyon a clear local command for rebuilding the compact Hermes skills
index after skill changes instead of waiting for a fresh session build.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _maybe_reexec_newer_python() -> None:
    if sys.version_info >= (3, 11):
        return
    candidates = [
        REPO_ROOT / ".venv" / "bin" / "python",
        Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "bin" / "python3",
        "python3.13",
        "python3.12",
        "python3.11",
    ]
    for candidate in candidates:
        path = Path(candidate) if isinstance(candidate, Path) else None
        resolved = ""
        if path is not None and path.exists():
            resolved = str(path)
        else:
            from shutil import which

            resolved = which(str(candidate)) or ""
        if not resolved:
            continue
        probe = subprocess.run(
            [resolved, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if probe.returncode == 0:
            os.execv(resolved, [resolved, str(Path(__file__).resolve()), *sys.argv[1:]])
    raise SystemExit("build_takyon_skills_index.py requires Python 3.11 or newer.")


_maybe_reexec_newer_python()

from agent.prompt_builder import (
    build_skills_system_prompt,
    clear_skills_system_prompt_cache,
)
from agent.skill_utils import parse_frontmatter
from tools.skills_sync import sync_skills


TAKYON_TOOLSETS = {"takyon", "web", "skills", "todo", "delegation"}
TAKYON_SKILLS_ROOT = REPO_ROOT / "skills" / "takyon"
CANONICAL_OUTPUT_ROOTS = {"distribution", "product", "research", "metrics"}


def _validate_takyon_skills() -> None:
    errors: list[str] = []
    for skill_md in sorted(TAKYON_SKILLS_ROOT.glob("*/SKILL.md")):
        rel = skill_md.relative_to(REPO_ROOT)
        try:
            frontmatter, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{rel}: {exc}")
            continue
        if not str(frontmatter.get("name") or "").strip():
            errors.append(f"{rel}: missing required frontmatter field 'name'")
        if not str(frontmatter.get("description") or "").strip():
            errors.append(f"{rel}: missing required frontmatter field 'description'")
        metadata = frontmatter.get("metadata") if isinstance(frontmatter.get("metadata"), dict) else {}
        takyon_meta = metadata.get("takyon") if isinstance(metadata.get("takyon"), dict) else {}
        allowed_roots = takyon_meta.get("allowed_roots")
        output_root = str(takyon_meta.get("output_root") or "").strip()
        publication = takyon_meta.get("publication")
        if not isinstance(allowed_roots, list) or not allowed_roots:
            errors.append(f"{rel}: metadata.takyon.allowed_roots must be a non-empty YAML list")
            allowed_roots_list: list[str] = []
        else:
            allowed_roots_list = [str(item).strip() for item in allowed_roots if str(item).strip()]
        if output_root not in CANONICAL_OUTPUT_ROOTS:
            errors.append(f"{rel}: metadata.takyon.output_root must be one of {sorted(CANONICAL_OUTPUT_ROOTS)}")
        if output_root and allowed_roots_list and output_root not in allowed_roots_list:
            errors.append(f"{rel}: metadata.takyon.output_root must also appear in allowed_roots")
        for root in allowed_roots_list:
            if root not in CANONICAL_OUTPUT_ROOTS:
                errors.append(f"{rel}: metadata.takyon.allowed_roots contains non-canonical root '{root}'")
        if not isinstance(publication, list) or not publication:
            errors.append(f"{rel}: metadata.takyon.publication must be a non-empty YAML list")
        else:
            for output in publication:
                output_text = str(output).strip()
                if not output_text:
                    errors.append(f"{rel}: metadata.takyon.publication contains an empty entry")
                    continue
                top = output_text.split("/", 1)[0]
                if top not in CANONICAL_OUTPUT_ROOTS:
                    errors.append(f"{rel}: publication path '{output_text}' is outside canonical roots")
                if allowed_roots_list and top not in allowed_roots_list:
                    errors.append(f"{rel}: publication path '{output_text}' is not inside allowed_roots {allowed_roots_list}")
    if errors:
        raise SystemExit("Takyon skill validation failed:\n- " + "\n- ".join(errors))


def main() -> int:
    _validate_takyon_skills()
    sync_result = sync_skills(quiet=True)
    clear_skills_system_prompt_cache(clear_snapshot=True)
    prompt = build_skills_system_prompt(available_toolsets=TAKYON_TOOLSETS)
    payload = {
        "synced": {
            "copied": sync_result.get("copied", []),
            "updated": sync_result.get("updated", []),
            "user_modified": sync_result.get("user_modified", []),
            "cleaned": sync_result.get("cleaned", []),
            "total_bundled": sync_result.get("total_bundled", 0),
        },
        "toolsets": sorted(TAKYON_TOOLSETS),
        "skills_index_built": bool(prompt),
        "skills_index_lines": len(prompt.splitlines()) if prompt else 0,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
